# -*- coding: utf-8 -*-
"""BTFRuntime 纵切集成（04 §8.3.8；M1 任务 4.1）。

config（四层合并+Schema）→ registry（协商+组装）→ Engine 全链路；
M1 留白显式化（risk 接线 / mirror_json 已随 M2/M3 交付；指数宇宙 → v0.5 报错指路）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION
from btf.experiment.store import LocalRunStore
from btf.runtime import BTFRuntime

from tests.fixtures.scenarios import t_plus1_scenario

FEED_NAME = "vertical_test_feed"


@pytest.fixture()
def runtime_config():
    scen = t_plus1_scenario()
    feed = scen.feed

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
            "universe": {"source": "explicit",
                         "symbols": ["000001.SZ", "511380.SH"]},
            "period": {"start": "2015-01-05", "end": "2015-01-09"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": FEED_NAME},
        # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
        "risk": {"rules": [], "allow_empty_chain": True},
    }


@pytest.mark.l2
class TestRuntimeVertical:
    def test_end_to_end(self, runtime_config, tmp_path):
        rt = BTFRuntime().load_config(runtime_config).build()
        assert rt.feed is not None
        assert rt.strategy.symbol == "000001.SZ"            # module:Class + params
        assert set(rt.instruments) == {"000001.SZ", "511380.SH"}
        # store 注入临时目录（默认落盘到 回测产物/runs，测试隔离）
        result = rt.run(store=LocalRunStore(tmp_path))
        assert len(result.snapshots) == 5
        assert len(result.fills) == 1
        assert result.fills[0].symbol == "000001.SZ"
        assert result.fills[0].qty == 90_000               # 0.9×1M/收盘10.0
        assert result.fills[0].price == pytest.approx(10.1)   # T+1 开盘成交
        assert result.snapshots[-1].total_value > 0

    def test_config_yaml_file_source(self, runtime_config, tmp_path):
        """YAML 文件路径源（L3 真实链路）。"""
        import yaml

        p = tmp_path / "backtest.yaml"
        p.write_text(yaml.safe_dump(runtime_config, allow_unicode=True),
                     encoding="utf-8")
        rt = BTFRuntime().load_config(p).build()
        assert rt.instruments["000001.SZ"].lot_size == 100

    def test_env_layer_reaches_runtime(self, runtime_config):
        rt = BTFRuntime().load_config(
            runtime_config,
            environ={"BTF_EXECUTION__COST_MODEL": "flat_rate"}).build()
        assert type(rt.cost_model).__name__ == "FlatRateCostModel"


@pytest.mark.l2
class TestRuntimeExplicitGaps:
    """M1 留白必须显式报错（防静默跳过）。"""

    def test_unknown_risk_rule_name(self, runtime_config):
        runtime_config["risk"] = {"rules": [{"name": "position_limit"}]}
        with pytest.raises(registry.RegistryError, match="risk_rule"):
            BTFRuntime().load_config(runtime_config).build()

    def test_mirror_json_rules_provider(self, runtime_config):
        """rules.source=mirror_json（M3 任务 6.1）：镜像 JSON → RulesProvider。"""
        mirror = Path(r"D:\量化策略\赤潮\rules_mirror_v77.json")
        if not mirror.is_file():
            pytest.skip("规则镜像未导出（先跑 tools/export_rules_mirror.py）")
        runtime_config["rules"] = {"source": "mirror_json", "path": str(mirror)}
        rt = BTFRuntime().load_config(runtime_config).build()
        assert rt.rules_provider.rules_version().startswith("v7.7@sha256:")
        assert rt.rules_provider.get("L2_filter")["pe_max"] == 30.0

    def test_index_universe_source_invalid_points_the_way(self, runtime_config):
        """source 写指数名（旧误用）→ 显式报错并指路正确写法。"""
        runtime_config["run"]["universe"] = {"source": "hs300"}
        with pytest.raises(Exception, match=r"未支持"):
            BTFRuntime().load_config(runtime_config).build()

    def test_load_run_roundtrip(self, runtime_config, tmp_path):
        """load_run（M2 任务 5.5 交付）：落盘产物可重载（manifest + 指标）。"""
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(runtime_config).build()
        run_id = rt.run(store=store).run_id
        bundle = rt.load_run(run_id, store=store)
        assert bundle.manifest.run_id == run_id
        assert bundle.manifest.status == "COMPLETED"
        assert len(bundle.snapshots) == 5
        assert bundle.metrics["n_fills"] == 1.0
