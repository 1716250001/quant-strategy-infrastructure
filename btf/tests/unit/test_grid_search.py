# -*- coding: utf-8 -*-
"""L1/L2 单测：grid_search 优化器（v0.5 V5-3；registry OPTIMIZER + CLI）。

验收（18 号 V5-3）：网格回测一致性（同组合 == 单跑指标）+ 名字表注册 +
CLI 冒烟。B4 性能基准见 tests/benchmark/test_b4_grid_search.py。
"""
from __future__ import annotations

import pytest
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION
from btf.optimize.grid import GridSearchOptimizer, deep_set

from tests.fixtures.scenarios import t_plus1_scenario

pytestmark = [pytest.mark.l1]

FEED_NAME = "optimize_test_memory_feed"


@pytest.fixture(scope="module")
def memory_config() -> dict:
    """MemoryFeed 注入的 5 日回测配置（进程池 worker 经 registry 名字取回）。"""
    feed = t_plus1_scenario().feed

    def factory(**_params):
        return feed

    factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, factory)

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
    }


class TestDeepSet:
    def test_nested_set_does_not_mutate_input(self):
        cfg = {"run": {"params": {"top_n": 5}}}
        out = deep_set(cfg, ("run", "params", "top_n"), 10)
        assert cfg["run"]["params"]["top_n"] == 5       # 原件不变
        assert out["run"]["params"]["top_n"] == 10

    def test_creates_missing_branch(self):
        out = deep_set({}, ("a", "b", "c"), 1)
        assert out["a"]["b"]["c"] == 1


class TestGridSearch:
    def test_registry_names(self):
        assert registry.available(registry.OPTIMIZER) == ["grid_search"]

    def test_grid_matches_single_run(self, memory_config):
        """一致性验收：同参数网格组合的指标 == 进程内单跑（同链路确定性）。"""
        from btf.analytics.metrics import compute_all
        from btf.runtime import BTFRuntime

        grid = {"run.params.weight": [0.5, 0.9]}
        report = GridSearchOptimizer(workers=0).run(
            memory_config, grid, objective="final_nav")
        assert report["n_combos"] == 2
        assert [r["params"]["run.params.weight"] for r in report["results"]] \
            == [0.5, 0.9]                      # 结果序 = 网格序（确定性）

        single = BTFRuntime().load_config(
            dict(memory_config), overrides={"run": {"params": {"weight": 0.9}}}
        ).build().run(persist=False)
        expected = compute_all(single.snapshots, single.fills, {})
        got = next(r for r in report["results"]
                   if r["params"]["run.params.weight"] == 0.9)
        assert got["metrics"]["final_nav"] == expected["final_nav"]
        assert got["metrics"]["total_return"] == expected["total_return"]
        assert got["n_fills"] == len(single.fills)

    def test_ranking_and_nan_last(self, memory_config):
        """objective 排名降序；NaN/缺失 → 末尾（诚实降级不剔除）。"""
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.9, 0.5]},
            objective="sharpe_ratio")           # 5 日两单 → sharpe 可能为 NaN
        ranks = [r["rank"] for r in report["results"]]
        assert sorted(ranks) == [1, 2]
        for item in report["results"]:
            value = item["metrics"].get("sharpe_ratio")
            if value != value:                  # NaN → 末位
                assert item["rank"] == max(ranks)

    def test_empty_grid_rejected(self, memory_config):
        with pytest.raises(ValueError, match="grid"):
            GridSearchOptimizer(workers=1).run(memory_config, {})
        with pytest.raises(ValueError, match="grid"):
            GridSearchOptimizer(workers=1).run(
                memory_config, {"run.params.weight": []})


class TestCliOptimize:
    def test_cli_smoke(self, memory_config, tmp_path, capsys):
        """CLI 冒烟：bt optimize（MemoryFeed 快场景 + 2 组合 × 1 进程）。"""
        import yaml
        from btf.cli.main import main

        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True),
                        encoding="utf-8")
        assert main(["optimize", "--config", str(path),
                     "--grid", "run.params.weight=0.5,0.9",
                     "--objective", "final_nav", "--workers", "0",
                     "--top", "2"]) == 0
        out = capsys.readouterr().out
        assert "grid_search" in out and "2 组" in out
        assert "run.params.weight=0.9" in out or "run.params.weight=0.5" in out

    def test_cli_bad_grid_exits_two(self, memory_config, tmp_path):
        import yaml
        from btf.cli.main import main

        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True),
                        encoding="utf-8")
        assert main(["optimize", "--config", str(path),
                     "--grid", "bad"]) == 2

    def test_cli_out_json(self, memory_config, tmp_path):
        import json

        import yaml
        from btf.cli.main import main

        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True),
                        encoding="utf-8")
        out_json = tmp_path / "result.json"
        assert main(["optimize", "--config", str(path),
                     "--grid", "run.params.weight=0.9",
                     "--objective", "final_nav", "--workers", "0",
                     "--out", str(out_json)]) == 0
        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert report["n_combos"] == 1 and report["objective"] == "final_nav"
