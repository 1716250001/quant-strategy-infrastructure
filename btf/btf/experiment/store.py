# -*- coding: utf-8 -*-
"""RunStore：run 目录产物 + 原子写 + 幂等读（08 §13.3/13.7；M2 任务 5.5）。

产物目录契约（08 §13.3）：
    {root}/{run_id}/
    ├── manifest.json      四版本 + rules_version（启动写骨架，结束补全）
    ├── snapshots.jsonl    PortfolioSnapshot 序列
    ├── trades.jsonl        Trade（FIFO 开平聚合）
    ├── fills.jsonl         Fill 明细（含 Fee 分项）
    ├── rejections.jsonl    拒单（order + code/message）
    ├── events.jsonl        事件溯源日志（引擎直写；可采样降级）
    └── metrics.json        15 项指标

**载体留痕**：08 §13.3 契约写 Parquet；本实现用 **JSONL/JSON**（零第三方
依赖——pyarrow 属数据层既有隐式依赖，未入 8/8 依赖预算；Parquet 载体与
列式压缩归预算重评后接入，目录/文件名契约保持不变，读取器按
contract_version 兼容）。

原子写：同目录 tmp 文件 + ``os.replace``（Windows/POSIX 均原子替换）——
崩溃后不会留下半截产物（08 §13.6 可审计性收口）。

幂等（08 §13.7）：同参数重跑产出**新 run_id**（历史不可变）；run_id 撞名
自动加序号后缀，绝不覆盖。
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from btf.config.paths import RUNS_DIR
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import ymd_of
from btf.experiment.manifest import RUNRESULT_CONTRACT, RunManifest

#: 产物文件名（08 §13.3）
MANIFEST = "manifest.json"
SNAPSHOTS = "snapshots.jsonl"
TRADES = "trades.jsonl"
FILLS = "fills.jsonl"
REJECTIONS = "rejections.jsonl"
EVENTS = "events.jsonl"
METRICS = "metrics.json"


# ─────────────────────────────────────────────────────────────
# 原子写
# ─────────────────────────────────────────────────────────────
def atomic_write_text(path: Path, text: str, *, fsync: bool = True) -> None:
    """原子写文本（tmp → **fsync** → os.replace ；父目录自动创建）。

    IO-10 / P2-1a（19 号附录 D.3；裁决六-2 后 0.X 唯一兜底即"落盘可信"）：
    原实现仅 tmp + `os.replace`——**无 fsync**：掉电后可能留下"名字已换、
    内容未落"的空文件（0.X 无版本回退，产物即证据）。今：
    ① 写后 `flush()` + `os.fsync(fd)`；② `os.replace` 后再对**父目录** fsync
    （POSIX 要求，目录项替换也需落盘）——Windows 上目录 fsync 不支持，
    按 OSError 静默跳过（平台差异而非静默失败）。
    `fsync=False` 仅留给"临时/可重建"文件（默认 True 覆盖全部产物）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        if fsync:
            fh.flush()
            os.fsync(fh.fileno())
    os.replace(tmp, path)
    if fsync:
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:                              # Windows：目录不可 os.open
            return
        try:
            os.fsync(dir_fd)                         # POSIX：目录项替换落盘
        except OSError:                              # pragma: no cover
            pass
        finally:
            os.close(dir_fd)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    """原子写 JSONL（整文件一次性；返回行数）。"""
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True, default=str)
             for r in rows]
    atomic_write_text(path, "".join(line + "\n" for line in lines))
    return len(lines)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ─────────────────────────────────────────────────────────────
# 鸭子类型编码（experiment 不 import portfolio/engine：只认字段）
# ─────────────────────────────────────────────────────────────
def _ymd(value: Any) -> str | None:
    """→ 'YYYYMMDD'（R4：口径改用 `domain.types.ymd_of` 单一真源）。

    `None` 保持返回 `None`（快照缺日期的既有语义不变）；其余形态交给
    `ymd_of`（TradingDate / date / 'YYYYMMDD' / 'YYYY-MM-DD' 皆可）。
    """
    if value is None:
        return None
    return ymd_of(value)


def _enc_fee(fee: Any) -> dict[str, float]:
    return {"commission": float(getattr(fee, "commission", 0.0) or 0.0),
            "stamp_duty": float(getattr(fee, "stamp_duty", 0.0) or 0.0),
            "transfer_fee": float(getattr(fee, "transfer_fee", 0.0) or 0.0),
            "total": float(getattr(fee, "total", 0.0) or 0.0)}


def _enc_fill(fill: Any) -> dict[str, Any]:
    return {
        "fill_id": fill.fill_id, "order_id": fill.order_id,
        "symbol": fill.symbol, "side": fill.side.value, "qty": fill.qty,
        "price": float(fill.price), "fee": _enc_fee(fill.fee),
        "fill_date": _ymd(fill.fill_date), "fill_timing": fill.fill_timing,
    }


def _enc_order(order: Any) -> dict[str, Any]:
    return {
        "order_id": order.order_id, "symbol": order.symbol,
        "side": order.side.value, "order_type": order.order_type.value,
        "qty": order.qty, "limit_price": order.limit_price,
        "created_at": _ymd(order.created_at), "tif": order.tif,
        "tag": order.tag,
    }


def _enc_snapshot(snap: Any) -> dict[str, Any]:
    return {
        "date": _ymd(snap.date),
        "cash": float(snap.cash), "market_value": float(snap.market_value),
        "total_value": float(snap.total_value),
        "daily_return": float(snap.daily_return),
        "cumulative_return": float(snap.cumulative_return),
        "drawdown": float(snap.drawdown),
        "weights": {k: float(v) for k, v in dict(snap.weights).items()},
        "positions_qty": {k: int(v) for k, v in dict(snap.positions_qty).items()},
    }


def _enc_trade(trade: Any) -> dict[str, Any]:
    return {
        "trade_id": trade.trade_id, "symbol": trade.symbol,
        "qty": trade.qty, "pnl": float(trade.pnl),
        "holding_days": trade.holding_days, "tag": trade.tag,
        "open_fill": _enc_fill(trade.open_fill) if trade.open_fill else None,
        "close_fill": _enc_fill(trade.close_fill) if trade.close_fill else None,
    }


def _enc_rejection(pair: Any) -> dict[str, Any]:
    """拒单：引擎产出 (Order, Rejection) 元组。"""
    order, rejection = pair if isinstance(pair, tuple) else (pair, None)
    out: dict[str, Any] = {"order": _enc_order(order)}
    if rejection is not None:
        out["code"] = rejection.code.value
        out["message"] = rejection.message
    return out


# ─────────────────────────────────────────────────────────────
# 读回记录（轻量类型：仅指标/报告所需字段，不重建 portfolio 对象）
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SnapshotRecord:
    """快照行（NAV 四式字段齐全——供指标重算与 R1 对账）。"""

    date: str
    cash: float
    market_value: float
    total_value: float
    daily_return: float
    cumulative_return: float
    drawdown: float
    weights: Mapping[str, float] = field(default_factory=dict)
    positions_qty: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RunBundle:
    """一次 run 的落盘产物视图（只读；报告/对比/R1 对账输入）。"""

    manifest: RunManifest
    metrics: dict[str, float]
    snapshots: list[SnapshotRecord]
    trades: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    rejections: list[dict[str, Any]]

    @property
    def run_id(self) -> str:
        return self.manifest.run_id


# ─────────────────────────────────────────────────────────────
# LocalRunStore
# ─────────────────────────────────────────────────────────────
class LocalRunStore:
    """本地目录 RunStore（registry RUN_STORE 扩展点：``local_jsonl``）。"""

    contract_version = CONTRACT_VERSION
    name = "local_jsonl"

    def __init__(self, root: Path | str = RUNS_DIR):
        self.root = Path(root)

    # ── 生命周期 ──
    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def create_run(
        self,
        config: Mapping[str, Any],
        *,
        run_id: str | None = None,
        data_fingerprint: str | None = None,
        rules_version: str = "none",
        rules_overrides: Sequence[Mapping[str, Any]] = (),
        assembly_notes: Sequence[str] = (),
        data_version: Mapping[str, Any] | None = None,
    ) -> RunManifest:
        """启动期：建目录 + **先写骨架 manifest**（崩溃可审计，08 §13.2）。

        `assembly_notes`（P1-NEW-5）：装配期口径披露（如 `source=all` 宇宙
        期间并集"跨期新增 N 只"）随产物落盘 → 报告披露区渲染（与
        `degraded_notes` 对称），否则宇宙口径只落日志、跨区间不可追溯。

        `data_version`（BB-1 重路）：runtime 实算的数据指纹真值
        （`content_hash` / `tables` / `anchor_date`），覆盖三层回退的占位值
        ——治 P2-NEW-7「空洞披露」（铁律新 16）。
        """
        manifest = RunManifest.skeleton(
            config, run_id=run_id, data_fingerprint=data_fingerprint,
            rules_version=rules_version, rules_overrides=rules_overrides,
            assembly_notes=assembly_notes, data_version=data_version)
        run_id = self._unique_run_id(manifest.run_id)
        if run_id != manifest.run_id:
            manifest = replace(manifest, run_id=run_id)
        self.write_manifest(manifest)
        return manifest

    def _unique_run_id(self, run_id: str) -> str:
        """幂等：撞名不覆盖（历史不可变）——追加 -2/-3 …"""
        if not self.run_dir(run_id).exists():
            return run_id
        for i in range(2, 1000):
            candidate = f"{run_id}-{i}"
            if not self.run_dir(candidate).exists():
                return candidate
        raise RuntimeError(f"run_id 撞名过多：{run_id}")

    def write_manifest(self, manifest: RunManifest) -> Path:
        """原子写 manifest（骨架/补全/失败态共用）。"""
        path = self.run_dir(manifest.run_id) / MANIFEST
        atomic_write_text(path, json.dumps(manifest.to_dict(), ensure_ascii=False,
                                           indent=2, sort_keys=True) + "\n")
        return path

    # ── 产物 ──
    def save_snapshots(self, run_id: str, snapshots: Sequence[Any]) -> int:
        return _write_jsonl(self.run_dir(run_id) / SNAPSHOTS,
                            [_enc_snapshot(s) for s in snapshots])

    def save_trades(self, run_id: str, trades: Sequence[Any]) -> int:
        return _write_jsonl(self.run_dir(run_id) / TRADES,
                            [_enc_trade(t) for t in trades])

    def save_fills(self, run_id: str, fills: Sequence[Any]) -> int:
        return _write_jsonl(self.run_dir(run_id) / FILLS,
                            [_enc_fill(f) for f in fills])

    def save_rejections(self, run_id: str, rejections: Sequence[Any]) -> int:
        return _write_jsonl(self.run_dir(run_id) / REJECTIONS,
                            [_enc_rejection(r) for r in rejections])

    def save_metrics(self, run_id: str, metrics: Mapping[str, float]
                     ) -> RunManifest:
        """写 metrics.json + **补全 manifest**（status=COMPLETED + digest）。

        数值守卫（**P0-NEW-7**，19 号 §24.3）：`metrics` 只允许**数值**——
        原实现 `float(v)` 无条件转换，一旦混入字符串/布尔（如
        `benchmark_symbol: '000300.SH'`）即在**回测跑完之后**抛
        `ValueError: could not convert string to float`（长区间白烧十几分钟）。
        今改为**快速失败 + 可操作报错**：指名违规键并给出处置建议（元信息走
        manifest/config_effective 或报告披露区，不混入数值指标集）。
        """
        bad = sorted(k for k, v in metrics.items()
                     if isinstance(v, bool) or not isinstance(v, (int, float)))
        if bad:
            raise ValueError(
                f"metrics 只允许数值，违规键 {bad}——元信息（如 "
                f"benchmark_symbol）请走 manifest/config_effective 或报告披露区"
                f"（P0-NEW-7：混入数值指标集会污染 metrics.json 与 R1 摘要）")
        # NaN 指标（04 §8.5 降级）落 null——严格 JSON 合规；读回还原为 NaN
        payload = {k: (None if v != v else float(v))
                   for k, v in sorted(metrics.items())}
        atomic_write_text(
            self.run_dir(run_id) / METRICS,
            json.dumps({"contract_version": RUNRESULT_CONTRACT,
                        "metrics": payload},
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return self.complete(run_id, metrics)

    def complete(self, run_id: str, metrics: Mapping[str, float]) -> RunManifest:
        manifest = self.read_manifest(run_id).completed(metrics)
        self.write_manifest(manifest)
        return manifest

    def mark_failed(self, run_id: str, reason: str) -> RunManifest:
        """失败终态（骨架→FAILED）：崩溃 run 留痕可审计。"""
        manifest = self.read_manifest(run_id).failed(reason)
        self.write_manifest(manifest)
        return manifest

    def events_path(self, run_id: str) -> Path:
        """events.jsonl 路径（引擎直写；08 §13.6 回放用）。"""
        return self.run_dir(run_id) / EVENTS

    def events_sampled(self, run_id: str) -> bool:
        """事件日志是否采样降级（含哨兵行 kind=_sampled——报告须披露）。"""
        path = self.events_path(run_id)
        if not path.is_file():
            return False
        with path.open(encoding="utf-8") as fh:
            return any('"_sampled"' in line for line in fh)

    # ── 读取 ──
    def read_manifest(self, run_id: str) -> RunManifest:
        path = self.run_dir(run_id) / MANIFEST
        if not path.is_file():
            raise FileNotFoundError(f"run 不存在或无 manifest：{run_id}")
        with path.open(encoding="utf-8") as fh:
            return RunManifest.from_dict(json.load(fh))

    def read_metrics(self, run_id: str) -> dict[str, float]:
        path = self.run_dir(run_id) / METRICS
        if not path.is_file():
            return {}
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh).get("metrics", {})
        return {k: (float("nan") if v is None else float(v))
                for k, v in raw.items()}

    def load(self, run_id: str) -> RunBundle:
        """读回全部产物（只读；报告再生成与 R1 对账入口）。"""
        directory = self.run_dir(run_id)
        return RunBundle(
            manifest=self.read_manifest(run_id),
            metrics=self.read_metrics(run_id),
            snapshots=[SnapshotRecord(**{k: v for k, v in row.items()})
                       for row in _read_jsonl(directory / SNAPSHOTS)],
            trades=_read_jsonl(directory / TRADES),
            fills=_read_jsonl(directory / FILLS),
            rejections=_read_jsonl(directory / REJECTIONS),
        )

    def list_runs(self) -> list[str]:
        """run_id 清单（按名字序；千级内线性扫描足够，08 §13.8）。"""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())


__all__ = [
    "EVENTS",
    "FILLS",
    "MANIFEST",
    "METRICS",
    "REJECTIONS",
    "SNAPSHOTS",
    "TRADES",
    "LocalRunStore",
    "RunBundle",
    "SnapshotRecord",
    "atomic_write_text",
]
