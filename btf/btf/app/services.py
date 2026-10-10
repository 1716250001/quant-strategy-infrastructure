# -*- coding: utf-8 -*-
"""应用服务层：CLI 与 API **共用**的编排逻辑（P2-3 / R4；19 号 §9.3）。

为什么需要（审计原话）：`cli/main.py` 与 `api.py` 存在 **8 组逐字重复**——
环境过滤、`--set` 类型推断、报告装配、dataset 子进程编排、`compute_all`
编排、日期 helper、嵌套路径写入……"改一处需同步多处"，且 api.py 承诺与
CLI **字节等价**（`api.py` 头部）→ 重复即分叉风险。

本模块是该承诺的落地形态：**唯一实现**放在 app，两个入口层只做参数解析与
转发（铁律新 10：cli/api 不得含编排逻辑）。已收敛的重复组：

| # | 重复组 | 原位置 | 现位置 |
|---|---|---|---|
| 1 | `_filtered_env` | `cli/main.py:34` ↔ `api.py:26` | `filter_btf_env()` |
| 2 | `--set` 嵌套解析 + 类型推断 | `cli/main.py:156-175` ↔ `grid.py` 第三份 | `parse_overrides()` / `parse_override_value()` |
| 3 | run 主链路（load→build→run） | `cli/main.py:186-214` ↔ `api.py:46-53` | `run_backtest()` |
| 4 | 报告装配（Assumptions+Builder+写盘） | `cli/main.py:260-285` ↔ `api.py:56-83` | `build_report_file()` |
| 5 | dataset 子进程编排 | `cli/main.py` ↔ `api.py:86-111` | `build_dataset()` |

**未收敛（明确留待后续批次）**：`compute_all` 三处调用链（runtime×2 +
grid）、日期 helper（runtime/store/state 各一）——二者与 R4 的
`BTFRuntime` 拆分同一批，见 19 号 §41.5 结论。
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = [
    "RunDetail",
    "RunOutcome",
    "RunSummary",
    "assemble_assumptions",
    "build_dataset",
    "build_report_file",
    "cold_backup",
    "filter_btf_env",
    "freeze_dataset",
    "freeze_payload",
    "list_runs",
    "parse_override_value",
    "parse_overrides",
    "run_backtest",
    "run_detail",
    "run_gate_check",
    "run_mc",
    "run_walk_forward",
]


@dataclass(frozen=True)
class RunOutcome:
    """一次回测编排的产物（runtime 供 CLI 披露用；result 供入口返回值）。

    `result is None` ⇔ dry-run（只装配）。
    """

    runtime: Any
    result: Any | None


def filter_btf_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """只把 `BTF_*` 环境变量交给配置层（测试/部署隔离；原两处逐字重复）。"""
    src = os.environ if environ is None else environ
    return {k: v for k, v in src.items() if k.startswith("BTF_")}


def parse_override_value(raw: str) -> Any:
    """CLI 字符串 → 配置值（**单源类型推断**：int → float → str）。

    与 `config.loader` 的合并层同口径（配置层负责最终类型校验）；`true/false`
    等布尔由配置层/`--set` 语义决定（此处不做隐式布尔化——避免 "1" 被误读）。
    """
    for caster in (int, float):
        try:
            return caster(raw)
        except ValueError:
            continue
    return raw


def parse_overrides(pairs: Sequence[str] | None) -> dict[str, Any]:
    """`--set a__b=1`（`__` 分隔嵌套）→ 覆盖 dict（L4 CLI 层）。"""
    out: dict[str, Any] = {}
    for pair in pairs or []:
        key, _, raw = pair.partition("=")
        if not key or not raw:
            raise ValueError(f"--set 需形如 a.b=1，得 {pair!r}")
        cursor = out
        parts = key.split("__")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = parse_override_value(raw)
    return out


def run_backtest(
    config: str | Path | Mapping[str, Any],
    *,
    overrides: Mapping[str, Any] | None = None,
    out: str | Path | None = None,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
) -> RunOutcome:
    """主链路（CLI/api 同源）：load_config → build → run（落盘）。

    返回 `RunOutcome`（runtime + result）：CLI 需要 runtime 打 `--verbose`
    分步耗时/披露摘要，api 只需要 result —— 同一次装配**不分叉**。
    `dry_run=True` → `result is None`（镜像 `bt run --dry-run`）。
    """
    from btf.runtime import BTFRuntime, make_store

    rt = BTFRuntime().load_config(config, environ=filter_btf_env(environ),
                                  overrides=overrides)
    rt.build()
    if dry_run:
        return RunOutcome(runtime=rt, result=None)
    return RunOutcome(runtime=rt, result=rt.run(store=make_store(out)))


def freeze_payload(info: Mapping[str, Any], *, period: Mapping[str, str],
                   root: str, created_at: str | None = None) -> dict[str, Any]:
    """冻结清单载荷（**CL-4**，19 号 §72.5 S-2）——字段即契约：

    `anchor_date` / `tables`（**名 ＋ 行数**＋日期域）/ `content_hash` /
    `mode` / `created_at`（＋ `period` / `root` / `btf_version` 与有效边界注）。

    `info` 必须来自 BB-1 实算（`BTFRuntime.dataset_fingerprint`）——R2
    单一真值源：`content_hash` 与 `manifest.data_version` / RC-14 门禁同源。
    """
    from btf._version import __version__

    detail = info.get("tables_detail") or {}
    tables = [
        {
            "name": str(name),
            "rows": int((detail.get(name) or {}).get("row_count") or 0),
            "date_min": (detail.get(name) or {}).get("date_min"),
            "date_max": (detail.get(name) or {}).get("date_max"),
        }
        for name in sorted(info.get("tables") or [])
    ]
    return {
        "anchor_date": info.get("anchor_date"),
        "tables": tables,
        "content_hash": info.get("content_hash"),
        "mode": info.get("mode"),
        "created_at": created_at or datetime.now().isoformat(timespec="seconds"),
        "period": {"start": str(period.get("start", "")),
                   "end": str(period.get("end", ""))},
        "root": str(root),
        "btf_version": __version__,
        "schema": "freeze.v1",
        "source": "BB-1 实算（btf.data.version.compute；与 RC-14 门禁同源）",
        "validity": ("S-2 只冻结「表清单 ＋ content_hash ＋ anchor_date」，"
                     "依赖原始库不被改写；原始库回填/重算后旧清单不可自证"
                     "（有效边界见 08 号 / 20 号）"),
    }


def write_freeze_manifest(payload: Mapping[str, Any],
                          out_dir: str | Path) -> dict[str, Any]:
    """落盘冻结清单（**只读**；R3 幂等/冲突——不静默覆写）。

    返回 `{path, action, payload}`：`action` = `created`（新建）/
    `idempotent`（同 `anchor_date` 且 `content_hash` 一致 ⇒ **不覆写**）。
    同 `anchor_date` 但哈希不同 ⇒ `FileExistsError`（**显式冲突**，不追加版本）。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    anchor = str(payload.get("anchor_date") or "unknown")
    path = out / f"{anchor}.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("content_hash") == payload.get("content_hash"):
            return {"path": path, "action": "idempotent", "payload": old}
        raise FileExistsError(
            f"冻结冲突：{path.name} 已存在且 content_hash 不一致——"
            f"旧 {old.get('content_hash')} / 新 {payload.get('content_hash')}；"
            f"S-2 不静默覆写（R3：显式冲突报错），请先归档旧清单")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IREAD)          # R3：落盘后置只读
    return {"path": path, "action": "created", "payload": dict(payload)}


def freeze_dataset(
    config: str | Path | Mapping[str, Any],
    *,
    out: str | Path | None = None,
    mode: str | None = None,
    overrides: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """`bt dataset freeze`（**CL-4**，19 号 §72.5）：数据不可变快照（S-2）。

    触发方式**取独立命令**（不挂 `bt run`）——理由：① 每次回测落清单会与 R3
    的「同 anchor 冲突语义」纠缠（同区间重复跑即冲突）；② 冻结是**显式动作**，
    独立命令的时间/口径可追溯。用法：回测前（或结论定稿时）对所用区间执行一次。

    实算经 `BTFRuntime.dataset_fingerprint`（BB-1 同源）；内存 Feed
    （`mode=in-memory`）无法冻结 ⇒ 显式报错（不静默产出假清单）。
    """
    from btf.config.paths import FREEZE_DIR
    from btf.runtime import BTFRuntime

    ovr: dict[str, Any] = {k: v for k, v in (overrides or {}).items()}
    if mode:
        ovr.setdefault("data", {})["fingerprint_mode"] = mode
    rt = BTFRuntime().load_config(config, environ=filter_btf_env(environ),
                                  overrides=ovr or None)
    rt.build()
    info = rt.dataset_fingerprint()
    if str(info.get("mode")) == "in-memory":
        raise ValueError(
            "内存 Feed 无文件级指纹（mode=in-memory）——冻结清单只对文件主库"
            "（tushare_parquet）有意义；不产出假清单（CL-4 / 铁律新 16）")
    payload = freeze_payload(info, period=rt.config["run"]["period"],
                             root=str(getattr(rt.feed, "root", "") or ""))
    return write_freeze_manifest(payload, out or FREEZE_DIR)


def assemble_assumptions(store: Any, bundle: Any) -> Any:
    """报告「假设与披露」装配（费率分段/规则版本/降级事件；CLI/api 同源）。

    **RC-03（2026-10-05）**：`matching` / `slippage` 一律读
    `manifest.config_effective`（生效配置）—— 原实现把两者**硬编码**为
    `"next_open（T+1 开盘撮合）"` / `"none（回测基线）"`：用户配了 `pct`
    报告仍显示 none，属**披露与事实相反**（13 §19.4 假设字段来源纪律）。
    """
    from btf.runtime import fee_segments
    from btf.viz.report import Assumptions, format_matching, format_slippage

    cfg = dict(getattr(bundle.manifest, "config_effective", None) or {})
    exec_cfg = cfg.get("execution") or {}
    slip_cfg = exec_cfg.get("slippage") or {}
    model = str(slip_cfg.get("model", "none"))
    degraded = ("事件日志已采样（replay 不完整）"
                if store.events_sampled(bundle.manifest.run_id) else "")
    return Assumptions(
        matching=format_matching(exec_cfg.get("handler", "next_open")),
        slippage=format_slippage(model, slip_cfg.get("params")),
        slippage_cost=slippage_cost_text(bundle, model),
        fee_segments=tuple(fee_segments()),
        rules_version=bundle.manifest.rules_version,
        rules_overrides=tuple(bundle.manifest.rules_overrides),
        degraded=(degraded,) if degraded else (),
    )


def slippage_cost_text(bundle: Any, model: str = "") -> str:
    """「价内滑点成本」披露串（**F9，2026-10-06**；13 §19.4 假设字段）。

    由 `bundle.fills` 的 `ref_price`（滑点前基准价）还原：

        Σ qty × |price − ref_price|   （按方向取不利侧，单位：元）

    并与**显性费用**（`Σ fee.total`）给出倍数——修正"`fee_ratio` 把占比较高的
    价内滑点成本排除在外"的披露缺口（19 号 §65.5；`final_nav` 差额 − 显性费用
    即为该项，实测 ≈2.08 倍于显性费用）。

    **诚实边界**：旧产物（无 `ref_price`）返回**明确不可得说明**，不静默算 0。
    """
    fills = list(getattr(bundle, "fills", None) or [])
    if not fills:
        return "无成交（0 元）"
    total = fees = 0.0
    missing = 0
    for f in fills:
        if isinstance(f, dict):
            ref, price, qty = f.get("ref_price"), f.get("price"), f.get("qty")
            side = f.get("side")
            fee = (f.get("fee") or {}).get("total")
        else:
            ref = getattr(f, "ref_price", None)
            price = getattr(f, "price", None)
            qty = getattr(f, "qty", None)
            side = getattr(getattr(f, "side", None), "value", None)
            fee = getattr(getattr(f, "fee", None), "total", None)
        if fee is not None:
            fees += float(fee)
        if ref is None or price is None or qty is None:
            missing += 1
            continue
        delta = float(price) - float(ref)
        if side == "sell":
            delta = -delta
        total += float(qty) * delta
    if missing == len(fills):
        return "不可得（产物未记录 ref_price——重跑后可披露）"
    notes: list[str] = []
    if model == "none":
        notes.append("零摩擦（滑点模型 none，显式声明）")
    if fees > 0:
        notes.append(f"= 显性费用 {fees:,.2f} 元的 {total / fees:.2f} 倍")
    if missing:
        notes.append(f"{missing}/{len(fills)} 笔无 ref_price 未计入")
    tail = f"（{'；'.join(notes)}）" if notes else ""
    return f"≈ {total:,.2f} 元{tail}"


def build_report_file(
    run_id: str,
    *,
    out: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> Path:
    """由产物再生成 HTML 报告（幂等；返回写盘路径）。"""
    from btf.runtime import BTFRuntime, make_store
    from btf.viz.report import ReportBuilder

    store = make_store(out_dir)
    bundle = BTFRuntime().load_run(run_id, store=store)
    html = ReportBuilder(bundle, assemble_assumptions(store, bundle)).build()
    target = (Path(out) if out
              else Path(store.run_dir(run_id)) / "report.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def _trading_days_of(config: Mapping[str, Any]) -> list[Any]:
    """交易日历（唯一真源；**RC-15**）：注入型 Feed 自带 `dates` 优先，否则 `trade_cal`。

    为什么不用自然月/区间天数：窗口切分必须落在**真实交易日**上（派单纪律：
    强制时间顺序、禁用随机划分）——`trade_cal`（is_open=1）是唯一真源；
    内存/注入型 Feed（测试）以其 `dates` 为准（同一语义）。
    """
    from btf import registry
    from btf.config.paths import MARKET_DATA_DIR
    from btf.data.quality import calendar_days
    from btf.domain.types import TradingDate

    data_cfg = dict(config.get("data") or {})
    period = dict((config.get("run") or {}).get("period") or {})
    start, end = str(period["start"]), str(period["end"])
    feed = None
    try:
        feed = registry.create(registry.DATA_FEED, str(data_cfg.get("feed")),
                               dict(data_cfg.get("feed_params") or {}))
    except Exception:            # 构造失败 ≠ 静默：走 trade_cal（缺失即 fail-closed）
        feed = None
    dates = getattr(feed, "dates", None)
    if dates:
        return [d for d in dates if start <= d.to_ymd() <= end]
    root = (dict(data_cfg.get("feed_params") or {}).get("root")
            or MARKET_DATA_DIR)
    # `calendar_days` 的口径是 **YYYYMMDD**（与 runtime 质检同参——`start_ymd`），
    # 而 period 是 `YYYY-MM-DD`：不转换会得到**空日历**（证据脚本实测抓出）
    return [TradingDate.from_ymd(d) for d in sorted(
        calendar_days(root, start.replace("-", ""), end.replace("-", "")))]


def run_walk_forward(
    config: str | Path,
    *,
    window: int,
    test: int | None = None,
    step: int | None = None,
    mode: str = "rolling",
    purge: int = 0,
    embargo: int = 0,
    persist: bool = True,
    warmup: int | None = None,
    out: str | Path | None = None,
    out_dir: str | Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Walk-Forward 编排（**RC-15**）：交易日历 → 逐折标准执行 → 全量分布。

    - 窗口切分**只用交易日历**（`_trading_days_of`；MemoryFeed 用其 `dates`）；
    - 每折走 `BTFRuntime` 标准装配与执行（与单跑同一代码路径），
      窗口信息进该折 manifest 装配披露（`persist=True` 时落盘）；
    - `--out` 落 JSON：`folds` 全量（含最差折）+ `summary`（mean/median/min/max）。
    """
    from btf.config.loader import load_config
    from btf.optimize.walkforward import run_walk_forward as _run_wf
    from btf.runtime import make_store

    test_eff = test if test is not None else step
    if test_eff is None:
        raise ValueError("须提供测试窗长（--test 或 --step）")
    if window is None or int(window) < 1:
        raise ValueError(f"--window（训练窗长）须 ≥ 1，得 {window!r}")
    cfg = load_config(Path(config), environ=filter_btf_env(environ),
                      overrides=dict(overrides or {}))
    days = _trading_days_of(cfg)
    report = _run_wf(
        cfg, days, train=int(window), test=int(test_eff),
        step=(int(step) if step is not None else None), mode=str(mode),
        purge=int(purge), embargo=int(embargo), persist=bool(persist),
        store=(make_store(out_dir) if persist else None),
        warmup=(None if warmup is None else int(warmup)))    # R58′：warm-up 透传
    if out:
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                     default=str) + "\n", encoding="utf-8")
    return report


def run_mc(
    run_id: str,
    *,
    n_resamples: int = 1000,
    n_permutations: int = 1000,
    seed: int | None = None,
    out: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """蒙特卡洛 / 置换检验编排（**RC-16**）：读 run 产物 → 分布披露。

    - **不进入引擎**（扰动只发生在收益序列层）⇒ 撮合规则逐位不变（09 #12）；
    - 种子取 `manifest.seed.master`（单源；`seed=` 显式覆盖）；
    - 产物：bootstrap CI（p05/p50/p95 + 是否跨 0）、置换检验双侧 p 值
      （sign/夏普 + order/最大回撤）、随机择时对照（需基准；无则 `null` 不伪造）。
    """
    from btf.analytics.bootstrap import (
        annualized_sharpe,
        bootstrap_ci,
        max_drawdown,
        noise_baseline,
        permutation_pvalue,
    )
    from btf.analytics.metrics import build_nav_series
    from btf.runtime import BTFRuntime, make_store

    store = make_store(out_dir)
    bundle = BTFRuntime().load_run(run_id, store=store)
    cfg = dict(bundle.manifest.config_effective or {})
    ann = int((cfg.get("analysis") or {}).get("annualization_factor", 252))
    master = int((bundle.manifest.seed or {}).get("master", 0) or 0)
    seed_used = master if seed is None else int(seed)
    returns = list(build_nav_series(bundle.snapshots).effective_returns())
    if len(returns) < 3:
        raise ValueError(
            f"样本不足（有效日收益 {len(returns)} 个 < 3）——MC/置换不适用")
    observed = {
        "sharpe": annualized_sharpe(returns, annualization=ann),
        "max_drawdown": max_drawdown(returns),
        "metrics": {k: bundle.metrics.get(k) for k in (
            "sharpe_ratio", "annualized_return", "total_return",
            "max_drawdown", "information_ratio", "annualized_excess")
            if k in bundle.metrics},
    }
    report: dict[str, Any] = {
        "run_id": run_id,
        "seed": {"master": master, "used": seed_used,
                 "source": "manifest" if seed is None else "explicit"},
        "n_periods": len(returns),
        "annualization": ann,
        "observed": observed,
        "bootstrap": bootstrap_ci(returns, n_resamples=int(n_resamples),
                                  seed=seed_used, annualization=ann),
        "permutation": {
            "sign_sharpe": permutation_pvalue(
                returns, scheme="sign", statistic="sharpe",
                n_permutations=int(n_permutations), seed=seed_used,
                annualization=ann),
            "order_max_drawdown": permutation_pvalue(
                returns, scheme="order", statistic="max_drawdown",
                n_permutations=int(n_permutations), seed=seed_used,
                annualization=ann),
        },
        "note": ("分布披露（p05/p50/p95 + 双侧 p 值）；只报数字不判定"
                 "（ADR-13）；扰动在收益序列层，撮合规则逐位不变（09 #12）"),
    }
    bench = (cfg.get("report") or {}).get("benchmark")
    n_hold = max(1, sum(1 for s in bundle.snapshots
                        if getattr(s, "positions_qty", None)))
    if bench:
        from btf.data.index_series import load_index_closes

        days = [str(s.date) for s in bundle.snapshots]
        _days, closes = load_index_closes(
            str(bench), days[0], days[-1], trading_days=days)
        pool = [closes[i] / closes[i - 1] - 1.0
                for i in range(1, len(closes)) if closes[i - 1]]
        report["noise_baseline"] = noise_baseline(
            pool, n_hold=n_hold, observed=observed["sharpe"],
            n_draws=int(n_resamples), seed=seed_used, statistic="sharpe",
            annualization=ann)
    else:
        report["noise_baseline"] = None
        report["noise_note"] = (
            "无基准（report.benchmark 未配）⇒ 随机择时对照不可得——null 而非"
            "伪造；配基准后可用（RC-16）")
    if out:
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                     default=str) + "\n", encoding="utf-8")
    return report


def scan_consolidation(
    config: str | Path,
    *,
    out: str | Path | None = None,
    tolerance: float = 0.005,
) -> dict[str, Any]:
    """`bt data scan-consolidation`（**R34′**；21 号 §4.13；ADR-20 第 5 条）。

    判据：**双证据**（证据 1 `pre_close(t)÷close(t−1)`；证据 2 `fund_adj`
    跳变**倒数** `f_{t−1}÷f_t`）＋容差 `tolerance`（缺省 0.5%）＋不符
    **fail-closed**（`ok=False`，不建模）；输出逐事件
    `ts_code / 事件日 / R / 证据1 / 证据2 / 偏差 / 结论` ＋**未建模项清单**。
    配置需 `run.universe.source='explicit'`（ETF/LOF 池）＋`run.period`。
    """
    import yaml

    from btf.config.paths import MARKET_DATA_DIR
    from btf.data.etf_corp_actions import scan_consolidations, trading_days_span
    from btf.domain.types import TradingDate, is_fund_code

    path = Path(config)
    if not path.is_file():
        raise FileNotFoundError(f"配置不存在：{path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    run = cfg.get("run") or {}
    period = run.get("period") or {}
    start_s, end_s = str(period.get("start") or ""), str(period.get("end") or "")
    if not start_s or not end_s:
        raise ValueError("配置缺 run.period.start/end（scan-consolidation 前置）")
    uni = run.get("universe") or {}
    if uni.get("source") != "explicit":
        raise ValueError(
            "scan-consolidation 需 run.universe.source='explicit'（ETF/LOF 池）"
            "——全市场/指数宇宙无 ETF 分摊语义")
    symbols = [str(s) for s in (uni.get("symbols") or [])]
    funds = [s for s in symbols if is_fund_code(s)]
    if not funds:
        raise ValueError(
            "scan-consolidation：显式宇宙内无 ETF/LOF 标的（is_fund_code 判定）")

    start = TradingDate.from_ymd(start_s.replace("-", ""))
    end = TradingDate.from_ymd(end_s.replace("-", ""))
    # **R51′（N55）**：交叉防线失能标志（`fund_div` 表缺失/不可读 ⇒ 除息日
    # 交叉判别不可用——与 `trade_cal` 同强度降级：告警 ＋ 退出码 2，禁静默放行）。
    cross_state: dict[str, Any] = {}
    events, notes = scan_consolidations(
        MARKET_DATA_DIR, funds, start, end, tolerance=tolerance,
        cross_state=cross_state)
    # 顺延/回退提示也一并输出（扫描器只报折算；分红侧由 R32′ 装载时披露）
    # **R47′（N51，2026-10-08）**：`trade_cal` 缺失 ⇒ **降级为告警**（不得
    # 未捕获异常）——`n_trading_days` 不可算；CLI 以**退出码 2** 提示
    # 「前置不完整」（非静默；扫描本身不依赖日历——交叉判别 only ex_date）。
    degraded: list[str] = []
    try:
        cal = trading_days_span(MARKET_DATA_DIR, start.to_ymd(), end.to_ymd())
    except FileNotFoundError as exc:
        cal = []
        degraded.append(
            f"trade_cal 缺失 ⇒ `n_trading_days` 不可算（降级；扫描不依赖日历"
            f"——R47′）：{exc}")
    if cross_state.get("disabled"):
        degraded.append(
            "交叉防线**不可用**（`fund_div` 表缺失/不可读）⇒ 折算候选**未经"
            "除息日判别**（幅度阈值单防线——N46 威胁面回归；R51′/N55）"
            "——请补 `fund_div` 后重跑")
    n_fail = sum(1 for e in events if not e.get("ok"))
    report = {
        "schema": "btf.data.scan_consolidation.v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "config": str(path),
        "tolerance": tolerance,
        "start": start.to_ymd(), "end": end.to_ymd(),
        "n_symbols": len(funds),
        "n_trading_days": (len(cal) if cal else None),
        "n_events": len(events), "n_fail_closed": n_fail,
        "events": events,
        "notes": notes,
        "degraded": degraded,
        # R51′/N55：交叉防线状态（disabled=true ⇒ 折算未经除息判别）
        "cross_disabled": bool(cross_state.get("disabled")),
        "cross_stats": dict(cross_state.get("stats") or {}),
    }
    if out:
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2)
                          + "\n", encoding="utf-8")
    return report


def build_dataset(
    cases: str = "g1",
    *,
    action: str = "build-golden",
    verify: bool = False,
    force: bool = False,
    list_only: bool = False,
    refcalc: bool = False,
    out: str | Path | None = None,
) -> int:
    """黄金集构建（委托 `tools/build_golden.py` 子进程；CLI/api 同源）。

    三个动作（CLI 审查报告-20260930 P3：把"构建/校验/重算"三条路显式化）：
        build-golden  构建（写盘；`force` 显式覆盖）
        verify        只与**主库**对账、不写盘（`--verify`）
        refcalc       只用**案例文件内嵌数据**独立重算断言（`--refcalc`，
                      不读主库——"黄金集自洽性"与"主库一致性"分开可证）
    """
    args: list[str] = []
    if refcalc:
        args += ["--cases", cases, "--refcalc"]
    elif list_only:
        args += ["--list"]
    else:
        args += ["--cases", cases]
        if verify:
            args.append("--verify")
        if force:
            args.append("--force")
    if out and (refcalc or not list_only):
        args += ["--out", str(out)]
    return _run_tool_script("build_golden.py", args)


# ─────────────────────────────────────────────────────────────
# 工具脚本委托（CLI 审查报告-20260930 P0/P2：门禁/冷备收编为子命令）
# ─────────────────────────────────────────────────────────────
def _run_tool_script(script_name: str, args: Sequence[str]) -> int:
    """子进程委托 `tools/<script_name>`（**单一真源**：CLI 只转发不做第二实现）。

    为什么不把判定逻辑搬进 `btf/`：门禁口径只能有一份——本项目文档长期写
    `bt check`，而它此前**不是** `bt` 子命令（文档与 CLI 脱节即是本函数要治
    的病）。委托保证 `bt check` 与 `python tools/check.py` 逐位同源，不存在
    "两条路各判一套"的漂移面。
    """
    script = Path(__file__).resolve().parents[2] / "tools" / script_name
    if not script.is_file():
        print(f"[error] 工具脚本缺失: {script}", file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, str(script), *args])


def run_gate_check() -> int:
    """`bt check`：十项架构门禁（委托 `tools/check.py`；退出码原样透传）。"""
    return _run_tool_script("check.py", [])


def cold_backup(
    *,
    reason: str | None = None,
    out: str | Path | None = None,
    verify: str | Path | None = None,
) -> int:
    """`bt cold-backup`：跨版本源码冷备（委托 `tools/cold_backup.py`）。

    `--verify DIR`（校验既有冷备）与"新建备份"互斥——脚本如此，故此处二选一；
    未给 `--reason` 时**不传**该参数（保留脚本默认值，避免 CLI 与脚本两套默认）。
    """
    args: list[str] = []
    if verify:
        args += ["--verify", str(verify)]
    else:
        if reason:
            args += ["--reason", str(reason)]
        if out:
            args += ["--out", str(out)]
    return _run_tool_script("cold_backup.py", args)


@dataclass(frozen=True)
class RunSummary:
    """`bt runs` 一行：run 目录摘要（只读；不改产物、不触发重算）。"""

    run_id: str
    status: str
    created: str                       # run 目录 mtime（本地时间）
    metrics: Mapping[str, float]
    note: str = ""                     # manifest/metrics 不可读时的原因


@dataclass(frozen=True)
class RunDetail:
    """`bt show --run <id>`：manifest + metrics 摘要（只读，不重算指标）。

    为什么不复用 `RunBundle`（`store.load`）：那会把 snapshots/fills 等
    全量 jsonl 读进内存（十年全市场 run 是百万行量级），而"看 manifest +
    指标"只需两个小文件——展示命令不应付读取全产物的代价。
    """

    run_id: str
    directory: Path
    manifest: Mapping[str, Any]
    metrics: Mapping[str, float]


def _dir_time(directory: Path) -> str:
    """run 目录 mtime → `YYYY-MM-DD HH:MM:SS`（不读 manifest：损坏目录也可列）。"""
    try:
        stamp = directory.stat().st_mtime
    except OSError:
        return "—"
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")


def list_runs(
    *,
    out_dir: str | Path | None = None,
    latest: int | None = None,
) -> list[RunSummary]:
    """列 RunStore 下的 run 摘要（按目录时间**新→旧**；`latest` 取前 N）。

    Store 经 `runtime.make_store` 取（铁律 5 / 新 10：入口层不直连 experiment）。
    manifest 缺失/损坏**不静默**：该行照列，`status` 退化为 `?`、`note` 写明
    原因——"列不出来"与"列出来但读不了"是两件事，后者必须可见（fail-visible）。
    """
    from btf.runtime import make_store

    store = make_store(out_dir)
    rows: list[RunSummary] = []
    for run_id in store.list_runs():
        directory = Path(store.run_dir(run_id))
        try:
            status, note = store.read_manifest(run_id).status, ""
        except (OSError, ValueError) as exc:       # 缺失/坏 JSON/契约不兼容
            status, note = "?", f"{type(exc).__name__}: {exc}"
        try:
            metrics: dict[str, float] = dict(store.read_metrics(run_id))
        except (OSError, ValueError) as exc:
            metrics = {}
            note = note or f"{type(exc).__name__}: {exc}"
        rows.append(RunSummary(run_id, status, _dir_time(directory), metrics,
                               note))
    rows.sort(key=lambda r: (r.created, r.run_id), reverse=True)
    return rows[:latest] if latest else rows


def run_detail(
    run_id: str,
    *,
    out_dir: str | Path | None = None,
) -> RunDetail:
    """读单个 run 的 manifest + metrics（只读；`run_id` 不存在 → FileNotFoundError）。"""
    from btf.runtime import make_store

    store = make_store(out_dir)
    manifest = store.read_manifest(run_id)
    return RunDetail(run_id=manifest.run_id, directory=Path(store.run_dir(run_id)),
                     manifest=manifest.to_dict(),
                     metrics=dict(store.read_metrics(run_id)))
