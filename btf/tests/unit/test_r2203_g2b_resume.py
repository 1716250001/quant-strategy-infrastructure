# -*- coding: utf-8 -*-
"""R2203 G2b：`bt optimize --resume` 断点续跑（原子写 / 幂等 / 同源校验）。"""

from __future__ import annotations

import json

import pytest
import yaml
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION
from btf.optimize.grid import GridSearchOptimizer

from tests.fixtures.scenarios import t_plus1_scenario

pytestmark = [pytest.mark.l1]

FEED_NAME = "optimize_resume_memory_feed"


@pytest.fixture(scope="module")
def memory_config() -> dict:
    """MemoryFeed 注入的 5 日回测配置（与 test_grid_search 同场景）。"""
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
        "risk": {"rules": [], "allow_empty_chain": True},
    }


def _state(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fresh_run_creates_state_and_second_run_is_idempotent(memory_config, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    grid = {"run.params.weight": [0.5, 0.9]}
    first = GridSearchOptimizer(workers=0).run(
        memory_config, grid, objective="final_nav", resume_path=state
    )
    assert first["n_combos"] == 2 and first["n_resumed"] == 0
    saved = _state(state)
    assert saved["schema"] == 1
    assert [item["index"] for item in saved["completed"]] == [0, 1]
    assert saved["objective"] == "final_nav"

    import btf.optimize.grid as grid_mod

    calls: list[object] = []
    real = grid_mod._run_single
    monkeypatch.setattr(
        grid_mod, "_run_single", lambda payload: calls.append(payload[1]) or real(payload)
    )
    second = GridSearchOptimizer(workers=0).run(
        memory_config, grid, objective="final_nav", resume_path=state
    )
    assert calls == []  # 幂等：零重复执行
    assert second["n_resumed"] == 2
    assert [r["params"] for r in second["results"]] == [r["params"] for r in first["results"]]
    assert [r["rank"] for r in second["results"]] == [r["rank"] for r in first["results"]]


def test_interrupted_run_only_executes_pending(memory_config, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    grid = {"run.params.weight": [0.5, 0.9]}
    GridSearchOptimizer(workers=0).run(
        memory_config, grid, objective="final_nav", resume_path=state
    )
    data = _state(state)
    data["completed"] = data["completed"][:1]  # 模拟第一组完成后中断
    state.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    import btf.optimize.grid as grid_mod

    calls: list[dict] = []
    real = grid_mod._run_single
    monkeypatch.setattr(
        grid_mod, "_run_single", lambda payload: calls.append(payload[1]) or real(payload)
    )
    report = GridSearchOptimizer(workers=0).run(
        memory_config, grid, objective="final_nav", resume_path=state
    )
    assert calls == [{"run.params.weight": 0.9}]
    assert report["n_resumed"] == 1
    assert len(_state(state)["completed"]) == 2
    assert [r["params"]["run.params.weight"] for r in report["results"]] == [0.5, 0.9]


def test_mismatched_grid_rejected(memory_config, tmp_path):
    state = tmp_path / "state.json"
    GridSearchOptimizer(workers=0).run(
        memory_config, {"run.params.weight": [0.5]}, objective="final_nav", resume_path=state
    )
    with pytest.raises(ValueError, match="不一致"):
        GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.9]}, objective="final_nav", resume_path=state
        )


def test_corrupt_state_rejected(memory_config, tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.5]}, objective="final_nav", resume_path=state
        )


def test_state_missing_metrics_rejected(memory_config, tmp_path):
    """N6：completed 项缺 metrics/objective ⇒ ValueError（不等到排名时 KeyError）。"""
    state = tmp_path / "state.json"
    GridSearchOptimizer(workers=0).run(
        memory_config, {"run.params.weight": [0.5]}, objective="final_nav", resume_path=state
    )
    data = _state(state)
    data["completed"][0].pop("metrics")
    state.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="metrics"):
        GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.5]}, objective="final_nav", resume_path=state
        )


def test_atomic_write_leaves_no_tmp(memory_config, tmp_path):
    state = tmp_path / "state.json"
    GridSearchOptimizer(workers=0).run(
        memory_config, {"run.params.weight": [0.5, 0.9]}, objective="final_nav", resume_path=state
    )
    assert list(tmp_path.glob("*.tmp*")) == []


class TestCliResume:
    def _write_config(self, memory_config, tmp_path):
        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True), encoding="utf-8")
        return path

    def _argv(self, cfg_path, state, grid_spec):
        return [
            "optimize",
            "--config",
            str(cfg_path),
            "--grid",
            grid_spec,
            "--objective",
            "final_nav",
            "--workers",
            "0",
            "--allow-short",
            "--resume",
            str(state),
        ]

    def test_cli_resume_second_run_skips_all(self, memory_config, tmp_path, capsys):
        from btf.cli.main import main

        cfg_path = self._write_config(memory_config, tmp_path)
        state = tmp_path / "state.json"
        argv = self._argv(cfg_path, state, "run.params.weight=0.5,0.9")
        assert main(argv) == 0
        assert "跳过已完成" not in capsys.readouterr().out
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert "跳过已完成 2 组" in out
        assert _state(state)["n_combos"] == 2

    def test_cli_mismatch_exits_two(self, memory_config, tmp_path, capsys):
        from btf.cli.main import main

        cfg_path = self._write_config(memory_config, tmp_path)
        state = tmp_path / "state.json"
        assert main(self._argv(cfg_path, state, "run.params.weight=0.5")) == 0
        assert main(self._argv(cfg_path, state, "run.params.weight=0.9")) == 2
        assert "不一致" in capsys.readouterr().err
