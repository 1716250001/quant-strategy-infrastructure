# -*- coding: utf-8 -*-
"""RunStore/manifest 单测（M2 任务 5.5；08 §13.2/13.3/13.7）。

覆盖（18 号 5.5 验收）：
    1. manifest 骨架先写（RUNNING）→ 结束补全（COMPLETED + metrics_digest）；
       崩溃可落 FAILED 终态；
    2. 四版本采集（data/code/config/env）+ rules_version + 脱敏；
    3. 产物目录契约（snapshots/trades/fills/rejections/events/metrics/manifest）
       + **原子写**（无 tmp 残留、全量替换）；
    4. **R1 复现**：同输入重跑 metrics_digest 一致；落盘产物重算 digest 一致；
       篡改指标 → digest 变化；
    5. 幂等：同参数重跑产新 run_id（撞名加后缀，历史不可变）；
    6. registry RUN_STORE 扩展点 + 版本协商；
    7. FIFO 开平配对（trades 产物源）。
"""
from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError

import pytest
from btf.analytics.metrics import compute_all
from btf.analytics.trades import pair_fills_to_trades
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.orders import (
    Fee,
    Fill,
    Order,
    OrderSide,
    OrderType,
    RejectCode,
    Rejection,
    Trade,
)
from btf.domain.types import TradingDate
from btf.experiment.manifest import (
    RUNRESULT_CONTRACT,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    RunManifest,
    collect_env_version,
    config_hash_of,
    metrics_digest_of,
    new_run_id,
)
from btf.experiment.store import (
    EVENTS,
    FILLS,
    MANIFEST,
    METRICS,
    REJECTIONS,
    SNAPSHOTS,
    TRADES,
    LocalRunStore,
    atomic_write_text,
)
from btf.portfolio.portfolio import Portfolio
from btf.registry import RUN_STORE, RegistryError, available, create

pytestmark = [pytest.mark.l1]

D = TradingDate.from_ymd
SYM = "000001.SZ"

CONFIG = {
    "schema_version": "backtest.v1",
    "run": {"strategy": "m:S", "period": {"start": "2015-01-05",
                                          "end": "2015-01-09"},
            "initial_cash": 1_000_000},
    "data": {"feed": "memory", "anchor_date": "2026-09-23",
             "token": "SECRET-TOKEN"},
    "seed": 20260926,
}


def mk_fill(side: OrderSide, qty: int, price: float, fee_total: float = 0.0,
            fid: str = "F1", day: str = "20150106") -> Fill:
    return Fill(fill_id=fid, order_id="O1", symbol=SYM, side=side, qty=qty,
                price=price,
                fee=Fee(commission=fee_total, stamp_duty=0.0,
                        transfer_fee=0.0, total=fee_total),
                fill_date=D(day), fill_timing="open")


def _snapshots(prices=(10.0, 12.0, 9.0, 10.0)) -> list:
    pf = Portfolio(cash=10_000.0)
    pf.apply_fill(mk_fill(OrderSide.BUY, 1_000, prices[0], 0.0, "F0", "20150105"))
    out = []
    for i, price in enumerate(prices):
        pf.mark_all({SYM: price})
        out.append(pf.snapshot(D(f"2015010{5 + i}")))
    return out


def _fills() -> list[Fill]:
    return [mk_fill(OrderSide.BUY, 1_000, 10.0, 5.0, "F1", "20150105"),
            mk_fill(OrderSide.SELL, 600, 11.0, 6.0, "F2", "20150107")]


def _rejections() -> list[tuple[Order, Rejection]]:
    order = Order(order_id="O2", symbol=SYM, side=OrderSide.BUY,
                  order_type=OrderType.MARKET, qty=100, limit_price=None,
                  created_at=D("20150106"))
    return [(order, Rejection(RejectCode.INSUFFICIENT_CASH, "现金不足"))]


# ═════════════════════════════════════════════════════════════
# manifest
# ═════════════════════════════════════════════════════════════
class TestManifest:
    def test_skeleton_is_running_without_digest(self):
        m = RunManifest.skeleton(CONFIG)
        assert m.status == STATUS_RUNNING
        assert m.metrics_digest is None
        assert m.contract_version == RUNRESULT_CONTRACT
        assert m.btf_contract_version == CONTRACT_VERSION

    def test_four_versions_present(self):
        m = RunManifest.skeleton(CONFIG)
        assert set(m.data_version) == {"dataset", "anchor_date", "content_hash",
                                       "tables", "completeness_level"}
        assert set(m.code_version) == {"git_commit", "dirty", "package_version"}
        assert m.config_hash.startswith("sha256:")
        assert set(m.env_version) == {"python", "libs", "platform"}
        assert m.seed == {"master": 20260926, "derived": {}}

    def test_config_effective_is_masked(self):
        """脱敏：token → ***（04 §8.5：敏感项不入 manifest 明文）。"""
        m = RunManifest.skeleton(CONFIG)
        assert m.config_effective["data"]["token"] == "***"

    def test_config_hash_stable_and_content_sensitive(self):
        a = config_hash_of(CONFIG)
        b = config_hash_of(dict(CONFIG, extra=1))
        assert a == config_hash_of(CONFIG)      # 确定性
        assert a != b                            # 内容敏感

    def test_data_version_uses_expected_hash_when_declared(self):
        cfg = {"data": {"feed": "memory", "expected_hash": "sha256:abc123"}}
        assert RunManifest.skeleton(cfg).data_version["content_hash"] == "sha256:abc123"
        assert RunManifest.skeleton({}).data_version["content_hash"] == "sha256:unknown"

    def test_env_version_actually_measured(self):
        """E2 纪律：运行时实采，不抄录文档。"""
        env = collect_env_version()
        assert env["python"].count(".") >= 1
        assert env["platform"]                # win32/linux/darwin
        assert isinstance(env["libs"], dict)  # 未安装则空，不伪造

    def test_seed_default_zero(self):
        assert RunManifest.skeleton({}).seed["master"] == 0

    def test_completed_and_failed(self):
        m = RunManifest.skeleton(CONFIG)
        done = m.completed({"total_return": 0.1})
        assert done.status == STATUS_COMPLETED
        assert done.metrics_digest == metrics_digest_of({"total_return": 0.1})
        bad = m.failed("ValueError: boom")
        assert bad.status == STATUS_FAILED and bad.error == "ValueError: boom"

    def test_roundtrip_dict(self):
        m = RunManifest.skeleton(CONFIG, rules_version="v7.7@sha256:ab12",
                                 rules_overrides=[{"rule_id": "L2", "value": 20}])
        back = RunManifest.from_dict(json.loads(json.dumps(m.to_dict())))
        assert back == m

    def test_run_id_format(self):
        """run_id = 时间戳 + config_hash 前 6 位（08 §13.2）。"""
        import datetime as dt
        cfg_hash = config_hash_of(CONFIG)
        rid = new_run_id(cfg_hash, dt.datetime(2026, 9, 26, 15, 30, 0))
        assert rid == f"20260926_153000_{cfg_hash.split(':')[-1][:6]}"


class TestMetricsDigest:
    """R1 校验锚（08 §13.2 metrics_digest）。"""

    def test_order_independent(self):
        a = {"x": 1.0, "y": 2.0}
        assert metrics_digest_of(a) == metrics_digest_of({"y": 2.0, "x": 1.0})

    def test_nan_normalized(self):
        assert metrics_digest_of({"a": float("nan")}) == metrics_digest_of(
            {"a": float("nan")})

    def test_value_change_changes_digest(self):
        assert metrics_digest_of({"a": 1.0}) != metrics_digest_of({"a": 1.0000001})

    def test_precision_stable(self):
        """浮点格式化稳定：0.1+0.2 与字面量 0.30000000000000004 同 digest。"""
        assert metrics_digest_of({"a": 0.1 + 0.2}) == metrics_digest_of(
            {"a": 0.30000000000000004})


# ═════════════════════════════════════════════════════════════
# store：产物 + 原子写 + 幂等
# ═════════════════════════════════════════════════════════════
class TestLocalRunStore:
    def test_create_run_writes_skeleton(self, tmp_path):
        store = LocalRunStore(tmp_path)
        m = store.create_run(CONFIG)
        assert (tmp_path / m.run_id / MANIFEST).is_file()
        assert (tmp_path / m.run_id).is_dir()
        assert store.read_manifest(m.run_id).status == STATUS_RUNNING

    def test_full_artifact_layout(self, tmp_path):
        store = LocalRunStore(tmp_path)
        m = store.create_run(CONFIG)
        snaps = _snapshots()
        fills = _fills()
        trades = pair_fills_to_trades(fills)
        assert store.save_snapshots(m.run_id, snaps) == 4
        assert store.save_fills(m.run_id, fills) == 2
        assert store.save_trades(m.run_id, trades) == 2
        assert store.save_rejections(m.run_id, _rejections()) == 1
        done = store.save_metrics(m.run_id, compute_all(snaps, fills))
        assert done.status == STATUS_COMPLETED and done.metrics_digest
        directory = tmp_path / m.run_id
        for name in (MANIFEST, SNAPSHOTS, TRADES, FILLS, REJECTIONS, METRICS):
            assert (directory / name).is_file(), name
        assert store.events_path(m.run_id) == directory / EVENTS

    def test_atomic_write_no_tmp_left(self, tmp_path):
        """原子写：tmp + replace——不残留 .tmp 文件。"""
        path = tmp_path / "a.json"
        atomic_write_text(path, "hello")
        atomic_write_text(path, "world")           # 覆盖（Windows 亦原子）
        assert path.read_text(encoding="utf-8") == "world"
        assert not list(tmp_path.glob("*.tmp*"))

    def test_atomic_write_creates_parent(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "x.json"
        atomic_write_text(path, "{}")
        assert json.loads(path.read_text(encoding="utf-8")) == {}

    def test_metrics_nan_roundtrip(self, tmp_path):
        """NaN 指标落 null（严格 JSON），读回还原 NaN。"""
        store = LocalRunStore(tmp_path)
        m = store.create_run(CONFIG)
        store.save_metrics(m.run_id, {"ok": 1.0, "degraded": float("nan")})
        raw = (tmp_path / m.run_id / METRICS).read_text(encoding="utf-8")
        assert "NaN" not in raw and "null" in raw
        back = store.read_metrics(m.run_id)
        assert back["ok"] == 1.0 and math.isnan(back["degraded"])

    def test_idempotent_new_run_id(self, tmp_path):
        """幂等（08 §13.7）：同参数重跑 → 新 run_id，历史不可变。"""
        store = LocalRunStore(tmp_path)
        first = store.create_run(CONFIG, run_id="fixed_000000")
        second = store.create_run(CONFIG, run_id="fixed_000000")
        assert first.run_id == "fixed_000000"
        assert second.run_id == "fixed_000000-2"
        assert (tmp_path / "fixed_000000").is_dir()

    def test_mark_failed_keeps_audit_trail(self, tmp_path):
        store = LocalRunStore(tmp_path)
        m = store.create_run(CONFIG)
        failed = store.mark_failed(m.run_id, "RuntimeError: boom")
        assert failed.status == STATUS_FAILED
        assert store.read_manifest(m.run_id).error == "RuntimeError: boom"

    def test_load_bundle(self, tmp_path):
        store = LocalRunStore(tmp_path)
        m = store.create_run(CONFIG)
        snaps, fills = _snapshots(), _fills()
        store.save_snapshots(m.run_id, snaps)
        store.save_fills(m.run_id, fills)
        store.save_trades(m.run_id, pair_fills_to_trades(fills))
        store.save_rejections(m.run_id, _rejections())
        store.save_metrics(m.run_id, compute_all(snaps, fills))
        bundle = store.load(m.run_id)
        assert bundle.run_id == m.run_id
        assert len(bundle.snapshots) == 4
        assert len(bundle.fills) == 2 and len(bundle.trades) == 2
        assert bundle.rejections[0]["code"] == "insufficient_cash"
        assert bundle.snapshots[0].total_value == pytest.approx(10_000.0)
        assert "total_return" in bundle.metrics

    def test_load_missing_run(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            LocalRunStore(tmp_path).load("nope")

    def test_list_runs(self, tmp_path):
        store = LocalRunStore(tmp_path)
        store.create_run(CONFIG, run_id="b_000000")
        store.create_run(CONFIG, run_id="a_000000")
        assert store.list_runs() == ["a_000000", "b_000000"]

    def test_empty_store_list(self, tmp_path):
        assert LocalRunStore(tmp_path / "none").list_runs() == []


# ═════════════════════════════════════════════════════════════
# R1 复现
# ═════════════════════════════════════════════════════════════
class TestR1Reproducibility:
    """R1（08 §13.1）：同 manifest 重跑 —— metrics_digest 一致。"""

    def _persist(self, store: LocalRunStore, run_id: str) -> RunManifest:
        snaps, fills = _snapshots(), _fills()
        store.save_snapshots(run_id, snaps)
        store.save_fills(run_id, fills)
        store.save_trades(run_id, pair_fills_to_trades(fills))
        store.save_rejections(run_id, _rejections())
        return store.save_metrics(run_id, compute_all(snaps, fills))

    def test_two_runs_same_digest(self, tmp_path):
        store = LocalRunStore(tmp_path)
        a = self._persist(store, store.create_run(CONFIG).run_id)
        b = self._persist(store, store.create_run(CONFIG).run_id)
        assert a.run_id != b.run_id
        assert a.metrics_digest == b.metrics_digest
        assert a.config_hash == b.config_hash

    def test_recompute_from_artifacts_matches_digest(self, tmp_path):
        """产物自洽：从落盘 snapshots/fills 重算 → 与 manifest digest 一致。"""
        store = LocalRunStore(tmp_path)
        m = self._persist(store, store.create_run(CONFIG).run_id)
        bundle = store.load(m.run_id)
        recomputed = compute_all(bundle.snapshots, fills_from_rows(bundle.fills))
        assert metrics_digest_of(recomputed) == m.metrics_digest

    def test_tampered_metric_changes_digest(self, tmp_path):
        store = LocalRunStore(tmp_path)
        m = self._persist(store, store.create_run(CONFIG).run_id)
        metrics = store.read_metrics(m.run_id)
        assert metrics_digest_of(metrics) == m.metrics_digest
        metrics["total_return"] += 1e-9
        assert metrics_digest_of(metrics) != m.metrics_digest

    def test_different_config_different_digest(self, tmp_path):
        """配置差异 → config_hash 不同（R2 可解释性基础）。"""
        store = LocalRunStore(tmp_path)
        other = {**CONFIG, "run": {**CONFIG["run"], "initial_cash": 2_000_000}}
        a = store.create_run(CONFIG)
        b = store.create_run(other)
        assert a.config_hash != b.config_hash


def fills_from_rows(rows: list[dict]) -> list[Fill]:
    """落盘 fills 行 → Fill（重算指标用；保持与引擎同构）。"""
    out = []
    for r in rows:
        fee = r["fee"]
        out.append(Fill(
            fill_id=r["fill_id"], order_id=r["order_id"], symbol=r["symbol"],
            side=OrderSide(r["side"]), qty=r["qty"], price=r["price"],
            fee=Fee(commission=fee["commission"], stamp_duty=fee["stamp_duty"],
                    transfer_fee=fee["transfer_fee"], total=fee["total"]),
            fill_date=D(r["fill_date"]), fill_timing=r["fill_timing"]))
    return out


# ═════════════════════════════════════════════════════════════
# registry 扩展点
# ═════════════════════════════════════════════════════════════
class TestRegistryRunStore:
    def test_registered(self):
        assert available(RUN_STORE) == ["local_jsonl"]

    def test_create_and_contract(self, tmp_path):
        store = create(RUN_STORE, "local_jsonl", {"root": str(tmp_path)})
        assert isinstance(store, LocalRunStore)
        assert store.contract_version == CONTRACT_VERSION
        assert store.root == tmp_path

    def test_unknown_name(self):
        with pytest.raises(RegistryError, match="未知插件名"):
            create(RUN_STORE, "local_parquet")


# ═════════════════════════════════════════════════════════════
# FIFO 开平配对（trades 产物源）
# ═════════════════════════════════════════════════════════════
class TestFifoPairing:
    def test_partial_close_splits_trade(self):
        """买 1000 → 卖 600：1 笔平仓 + 1 笔未平仓（400 股）。"""
        trades = pair_fills_to_trades(_fills())
        assert [t.qty for t in trades] == [600, 400]
        assert trades[0].close_fill is not None
        assert trades[1].close_fill is None and trades[1].pnl == 0.0

    def test_pnl_includes_both_side_fees(self):
        """pnl = 平仓收入 − 开仓成本，双边费用按数量比例摊入。"""
        trades = pair_fills_to_trades(_fills())
        got = trades[0]
        # 卖 600@11：收入 6600 − 卖费 6×(600/600)=6；买成本 600×10 + 5×(600/1000)=3
        assert got.pnl == pytest.approx(6600 - 6 - 6000 - 3)

    def test_holding_days(self):
        trades = pair_fills_to_trades(_fills())
        assert trades[0].holding_days == 2          # 20150105 → 20150107

    def test_fifo_order_across_lots(self):
        """两笔买入（先 500@10 后 500@12）→ 卖 600：先平第一笔全部再平第二笔 100。"""
        fills = [mk_fill(OrderSide.BUY, 500, 10.0, 0.0, "F1", "20150105"),
                 mk_fill(OrderSide.BUY, 500, 12.0, 0.0, "F2", "20150106"),
                 mk_fill(OrderSide.SELL, 600, 11.0, 0.0, "F3", "20150108")]
        trades = pair_fills_to_trades(fills)
        assert [(t.qty, t.open_fill.price, t.close_fill.price) for t in trades
                if t.close_fill] == [(500, 10.0, 11.0), (100, 12.0, 11.0)]
        assert sum(t.qty for t in trades if t.close_fill is None) == 400

    def test_deterministic_trade_ids(self):
        a = pair_fills_to_trades(_fills())
        b = pair_fills_to_trades(list(reversed(_fills())))   # 输入乱序
        assert [t.trade_id for t in a] == ["T000001", "T000002"]
        assert [(t.qty, t.pnl) for t in a] == [(t.qty, t.pnl) for t in b]

    def test_total_pnl_conservation(self):
        """总额守恒：Σ trade.pnl = 已实现口径合计（含双边费用）。"""
        fills = _fills()
        trades = pair_fills_to_trades(fills)
        sell = fills[1]
        realized = (sell.price - 10.0) * sell.qty - 6.0 - 5.0 * (600 / 1000)
        assert sum(t.pnl for t in trades) == pytest.approx(realized)

    def test_trade_is_frozen_domain_type(self):
        trades = pair_fills_to_trades(_fills())
        assert isinstance(trades[0], Trade)
        with pytest.raises(FrozenInstanceError):
            trades[0].qty = 0  # type: ignore[misc]
