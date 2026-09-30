# -*- coding: utf-8 -*-
"""RunStore 端到端验收（M2 任务 5.5；08 §13.1 R1 / §13.2 / §13.3）。

验收：
    1. R1 数值级复现：同一 config **两次落盘回测** → metrics_digest 一致
       （run_id 不同，历史不可变）；
    2. run 目录产物齐全（manifest/snapshots/trades/fills/rejections/events/metrics）；
    3. 崩溃 run：manifest 落 FAILED（骨架已写 → 可审计）；
    4. persist=False 不落盘（纯内存回测）。
"""
from __future__ import annotations

import pytest
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION
from btf.experiment.manifest import STATUS_COMPLETED, STATUS_FAILED
from btf.experiment.store import (
    EVENTS,
    FILLS,
    MANIFEST,
    METRICS,
    REJECTIONS,
    SNAPSHOTS,
    TRADES,
    LocalRunStore,
)
from btf.runtime import BTFRuntime
from btf.strategy.base import StrategyBase

pytestmark = [pytest.mark.l2]

FEED_NAME = "r1_test_feed"


@pytest.fixture()
def runtime_config():
    from tests.fixtures.scenarios import t_plus1_scenario

    feed = t_plus1_scenario().feed

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, _factory)
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": "2015-01-05", "end": "2015-01-09"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": FEED_NAME},
        # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
        "risk": {"rules": [], "allow_empty_chain": True},
        "seed": {"master": 20260926},        # Schema：seed 为对象（08 §13.4）
    }


class BoomStrategy(StrategyBase):
    """故意崩溃策略（崩溃审计用例）。"""

    contract_version = CONTRACT_VERSION    # S1 契约（19 号 P1-2 协商要求）

    def __init__(self, **_params):        # 装配期接受 run.params 注入
        super().__init__()

    def on_close(self, ctx, date):
        raise RuntimeError("boom")


class TestR1EndToEnd:
    def test_two_runs_same_metrics_digest(self, runtime_config, tmp_path):
        """R1：同 manifest 四元组重跑 → metrics_digest 浮点级一致。"""
        digests, ids = [], []
        for _ in range(2):
            store = LocalRunStore(tmp_path)
            rt = BTFRuntime().load_config(runtime_config).build()
            result = rt.run(store=store)
            digests.append(result.manifest.metrics_digest)
            ids.append(result.run_id)
        assert ids[0] != ids[1]                 # 历史不可变：新 run_id
        assert digests[0] == digests[1]          # R1 锚点
        assert digests[0]

    def test_artifact_layout_complete(self, runtime_config, tmp_path):
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(runtime_config).build()
        result = rt.run(store=store)
        directory = tmp_path / result.run_id
        for name in (MANIFEST, SNAPSHOTS, TRADES, FILLS, REJECTIONS, METRICS,
                     EVENTS):
            assert (directory / name).is_file(), name
        assert result.manifest.status == STATUS_COMPLETED
        assert result.metrics["n_trading_days"] == 5.0
        assert result.metrics["n_fills"] == 1.0

    def test_manifest_captures_seed_and_feed(self, runtime_config, tmp_path):
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(runtime_config).build()
        result = rt.run(store=store)
        m = result.manifest
        assert m.seed["master"] == 20260926
        assert m.data_version["dataset"] == FEED_NAME
        assert m.btf_contract_version == CONTRACT_VERSION

    def test_load_run_reads_back(self, runtime_config, tmp_path):
        """04 §8.3.8 load_run：产物重载（报告/对比/R1 对账入口）。"""
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(runtime_config).build()
        run_id = rt.run(store=store).run_id
        bundle = rt.load_run(run_id, store=store)
        assert bundle.run_id == run_id
        assert len(bundle.snapshots) == 5
        assert len(bundle.fills) == 1
        assert bundle.metrics == store.read_metrics(run_id)
        assert bundle.manifest.metrics_digest == rt.load_manifest(
            run_id, store).metrics_digest

    def test_crash_run_leaves_failed_manifest(self, runtime_config, tmp_path):
        """崩溃可审计（08 §13.2）：骨架已写 → 异常后落 FAILED + 原因。"""
        runtime_config["run"]["strategy"] = "tests.integration.test_run_r1:BoomStrategy"
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(runtime_config).build()
        with pytest.raises(RuntimeError, match="boom"):
            rt.run(store=store)
        runs = store.list_runs()
        assert len(runs) == 1
        manifest = store.read_manifest(runs[0])
        assert manifest.status == STATUS_FAILED
        assert "boom" in (manifest.error or "")

    def test_persist_false_writes_nothing(self, runtime_config, tmp_path):
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(runtime_config).build()
        result = rt.run(store=store, persist=False)
        assert len(result.snapshots) == 5
        assert store.list_runs() == []
