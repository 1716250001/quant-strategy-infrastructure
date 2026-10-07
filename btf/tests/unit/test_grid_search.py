# -*- coding: utf-8 -*-
"""L1/L2 单测：grid_search 优化器（v0.5 V5-3；registry OPTIMIZER + CLI）。

验收（18 号 V5-3）：网格回测一致性（同组合 == 单跑指标）+ 名字表注册 +
CLI 冒烟。B4 性能基准见 tests/benchmark/test_b4_grid_search.py。
"""
from __future__ import annotations

import pytest
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
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


class TestInferenceFieldsRc06:
    """RC-06（2026-10-05）：网格产物含 `N` / `param_dim` / `n_trials` /
    `psr` / `dsr` 与"未校正"提示；**排序与 rank 语义不变**（只加字段）。"""

    def test_fields_present(self, memory_config):
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.5, 0.9]},
            objective="final_nav")
        assert report["n_combos"] == 2
        assert report["param_dim"] == 1
        assert report["n_trials_declared"] == 0
        assert report["n_trials"] == 2
        assert report["n_trading_days"] == 5
        assert "N=2" in report["adjustment_note"]
        assert "0.05/N" in report["adjustment_note"]

    def test_n_trials_declared_adds_up(self, memory_config):
        """用户声明的"历史累计试验数"计入 N（越试越不可信）。"""
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.5, 0.9]},
            objective="final_nav", n_trials_declared=430)
        assert report["n_trials"] == 432
        assert "N=432" in report["adjustment_note"]

    def test_psr_dsr_fields(self, memory_config):
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.5, 0.9]},
            objective="final_nav")
        for item in report["results"]:
            assert "psr" in item and "dsr" in item
            if item["psr"] is not None:              # 样本不足 ⇒ None（不阻断）
                assert 0.0 < item["psr"] < 1.0
                assert item["dsr"] is not None
        assert set(report["top"][0]) >= {"rank", "params", "value", "psr", "dsr"}

    def test_ranking_semantics_unchanged(self, memory_config):
        """只加字段：rank 仍 = objective 降序（与既有行为逐位一致）。"""
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.9, 0.5]},
            objective="final_nav")
        by_rank = sorted(report["results"], key=lambda r: r["rank"])
        values = [r["metrics"]["final_nav"] for r in by_rank]
        assert values == sorted(values, reverse=True)


class TestCliOptimize:
    def test_cli_prints_n_and_adjustment_hint(self, memory_config, tmp_path,
                                              capsys):
        """RC-06：终端显式打印 `N=` 与校正阈值提示。"""
        import yaml
        from btf.cli.main import main

        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True),
                        encoding="utf-8")
        # CL-1：5 日样本 < MIN_OPTIMIZE_TRADING_DAYS ⇒ 须显式 --allow-short
        assert main(["optimize", "--config", str(path),
                     "--grid", "run.params.weight=0.5,0.9",
                     "--objective", "final_nav", "--workers", "0",
                     "--allow-short", "--n-trials", "10"]) == 0
        out = capsys.readouterr().out
        assert "N=12" in out
        assert "未校正" in out and "0.05/N" in out
        assert "psr" in out and "dsr" in out           # 表头列

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
                     "--allow-short", "--top", "2"]) == 0
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
                     "--allow-short", "--out", str(out_json)]) == 0
        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert report["n_combos"] == 1 and report["objective"] == "final_nav"


def _spiky_config(memory_config: dict) -> dict:
    """尖峰场景：`x` 在 [0.95, 1.05] 才建仓（tests/fixtures/spiky.py）。"""
    cfg = dict(memory_config)
    cfg["run"] = dict(cfg["run"])
    cfg["run"]["strategy"] = "tests.fixtures.spiky:ConfigSpiky"
    cfg["run"]["params"] = {"symbol": "000001.SZ", "x": 1.0}
    return cfg


class TestPerturbRc07:
    """RC-07（2026-10-05）：top1 邻域扫描——**双向对照**（尖峰必报敏感 /
    平坦必报不敏感）；产物只报数字，不替使用者判定。"""

    def test_spiky_grid_reports_sensitive(self, memory_config):
        """尖峰最优：δ=0.04 的点仍在带内（收益 = base）、δ=0.1 的点落带外
        （收益 0）⇒ 极差 > 0、相对退化 = 100% > 50%。"""
        report = GridSearchOptimizer(workers=0).run(
            _spiky_config(memory_config), {"run.params.x": [0.5, 1.0]},
            objective="total_return", perturb=[0.04, 0.1])
        block = report["perturbation"]
        assert block["base_params"]["run.params.x"] == 1.0
        assert block["base_value"] > 0
        assert block["n_points"] == 4               # 0.9 / 1.1 / 0.96 / 1.04
        assert block["relative_degradation"] > 0.5
        assert block["sensitive"] is True
        assert block["range"] > 0 and block["std"] > 0

    def test_flat_grid_reports_insensitive(self, memory_config):
        """平坦对照：买持收益随权重近似线性 ⇒ 退化 ≈ 10% < 50%。"""
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.5, 0.9]},
            objective="total_return", perturb=[0.1])
        block = report["perturbation"]
        assert block["n_points"] == 2               # 0.81 / 0.99
        assert block["relative_degradation"] < 0.5
        assert block["sensitive"] is False

    def test_no_perturb_arg_keeps_block_none(self, memory_config):
        """不传 `perturb` ⇒ `perturbation` 为 None（既有产物形态不漂移）。"""
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.9]}, objective="final_nav")
        assert report["perturbation"] is None

    def test_non_numeric_top1_skipped(self, memory_config):
        """top1 参数非数值（str）⇒ 不扫描、不崩（note 说明，不伪装成"不敏感"）。"""
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"execution.cost_model": ["tiered_v1"]},
            objective="final_nav", perturb=[0.1])
        block = report["perturbation"]
        assert block["n_points"] == 0
        assert block["sensitive"] is None           # **不得**伪装为 False
        assert "无数值型参数" in block["note"]

    def test_int_param_rounds_and_skips_same(self, memory_config):
        """整型参数取就近整数；取整后与原值相同则跳过（不产非法小数）。"""
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.initial_cash": [1_000_000]},
            objective="final_nav", perturb=[0.1])
        block = report["perturbation"]
        # 1e6×0.9 / 1e6×1.1 均为整数；schema 可接受
        assert block["n_points"] == 2
        assert all(isinstance(p["params"]["run.initial_cash"], int)
                   for p in block["points"])

    def test_bad_delta_rejected(self, memory_config):
        with pytest.raises(ValueError, match="perturb"):
            GridSearchOptimizer(workers=0).run(
                memory_config, {"run.params.weight": [0.9]},
                objective="final_nav", perturb=[1.5])

    def test_cli_perturb_prints_and_warns(self, memory_config, tmp_path,
                                          capsys):
        """真入口：`bt optimize --perturb`（尖峰场景）⇒ 打印统计 + 过拟合提示。"""
        import yaml
        from btf.cli.main import main

        cfg = _spiky_config(memory_config)
        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True),
                        encoding="utf-8")
        assert main(["optimize", "--config", str(path),
                     "--grid", "run.params.x=0.5,1.0",
                     "--objective", "total_return", "--workers", "0",
                     "--allow-short", "--perturb", "0.1"]) == 0
        out = capsys.readouterr().out
        assert "perturb" in out and "相对退化" in out
        assert "疑似过拟合" in out

    def test_cli_bad_perturb_exits_two(self, memory_config, tmp_path):
        import yaml
        from btf.cli.main import main

        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True),
                        encoding="utf-8")
        assert main(["optimize", "--config", str(path),
                     "--grid", "run.params.weight=0.9",
                     "--perturb", "1.5"]) == 2

    def test_cli_flat_no_overfit_warn(self, memory_config, tmp_path, capsys):
        """反向探针：平坦网格 ⇒ CLI **不得**出现「疑似过拟合」（防告警恒现）。"""
        import yaml
        from btf.cli.main import main

        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True),
                        encoding="utf-8")
        assert main(["optimize", "--config", str(path),
                     "--grid", "run.params.weight=0.5,0.9",
                     "--objective", "total_return", "--workers", "0",
                     "--allow-short", "--perturb", "0.1"]) == 0
        out = capsys.readouterr().out
        assert "perturb" in out and "相对退化" in out
        assert "疑似过拟合" not in out


class TestGridExtrasRc08:
    """RC-08（2026-10-05）：网格组合指标与 `bt run` 同口径（含基准相对项）。"""

    def test_grid_metrics_include_benchmark_extras(self, memory_config,
                                                   monkeypatch):
        """配基准（此处以 monkeypatch 模拟，避免依赖主库）⇒ 每组合含 IR/超额，
        且可作为 `--objective information_ratio` 排序键。"""
        from btf.runtime import BTFRuntime

        monkeypatch.setattr(
            BTFRuntime, "_benchmark_metrics",
            lambda self, snaps: {"information_ratio": 0.42,
                                 "annualized_excess": 0.031})
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.9]},
            objective="information_ratio")
        metrics = report["results"][0]["metrics"]
        assert metrics["information_ratio"] == 0.42
        assert metrics["annualized_excess"] == 0.031
        assert report["top"][0]["value"] == 0.42

    def test_no_benchmark_no_error_and_no_extra_keys(self, memory_config):
        """未配 `report.benchmark` ⇒ extras 返回空：不报错、不新增字段。"""
        report = GridSearchOptimizer(workers=0).run(
            memory_config, {"run.params.weight": [0.9]},
            objective="final_nav")
        metrics = report["results"][0]["metrics"]
        assert "information_ratio" not in metrics
        assert "annualized_excess" not in metrics

    def test_cli_objective_ir_without_benchmark_no_error(self, memory_config,
                                                         tmp_path):
        """未配基准时 `--objective information_ratio` 保持现状**不报错**。"""
        import yaml
        from btf.cli.main import main

        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True),
                        encoding="utf-8")
        assert main(["optimize", "--config", str(path),
                     "--grid", "run.params.weight=0.5,0.9",
                     "--objective", "information_ratio",
                     "--workers", "0", "--allow-short"]) == 0


# ─────────────────────────────────────────────
# CL-1（19 号 §72.2 / 报告 D 篇 P2-5）：optimize 短区间门禁
# ─────────────────────────────────────────────
LONG_FEED_NAME = "optimize_long_memory_feed"


def _business_days(n: int) -> list[TradingDate]:
    """n 个连续工作日（周一~周五；测试自备日历，不依赖交易所日历）。"""
    from datetime import date, timedelta

    out: list[TradingDate] = []
    cur = date(2015, 1, 5)
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(TradingDate(cur))
        cur += timedelta(days=1)
    return out


@pytest.fixture(scope="module")
def long_memory_config() -> dict:
    """260 个交易日（≥ MIN_OPTIMIZE_TRADING_DAYS）的 MemoryFeed 配置。"""
    from btf.data.memory import MemoryFeed

    from tests.fixtures.scenarios import make_bar, make_state, t_plus1_scenario

    dates = _business_days(260)
    bars = []
    states: dict = {}
    for d in dates:
        bars.append(make_bar("000001.SZ", d, 10.0, 10.0, 10.0, 10.0, 10.0))
        states[(d.to_ymd(), "000001.SZ")] = make_state("000001.SZ", d, pre=10.0)
    instruments = {"000001.SZ": t_plus1_scenario().instruments["000001.SZ"]}
    feed = MemoryFeed(bars, states, instruments, dates=dates)

    def factory(**_params):
        return feed

    factory.contract_version = CONTRACT_VERSION
    if LONG_FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, LONG_FEED_NAME, factory)

    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": dates[0].iso.isoformat(),
                       "end": dates[-1].iso.isoformat()},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": LONG_FEED_NAME},
        "risk": {"rules": [], "allow_empty_chain": True},
    }


class TestShortPeriodGateCl1:
    """CL-1（19 号 §72.2 / 报告 D 篇 P2-5）：`bt optimize` 短区间**默认阻断**；
    显式 `--allow-short` 放行并把 override（n_days/阈值）记入产物。"""

    def test_short_period_blocked_by_default(self, memory_config, tmp_path,
                                             capsys):
        """判据 A：n_days < 阈值 ⇒ 非零退出（3），stdout 含阈值/当前 n_days/
        去路，且**不打印任何排序表**。"""
        import yaml
        from btf.cli.main import main

        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True),
                        encoding="utf-8")
        rc = main(["optimize", "--config", str(path),
                   "--grid", "run.params.weight=0.5,0.9",
                   "--objective", "final_nav", "--workers", "0"])
        out = capsys.readouterr().out
        assert rc == 3
        assert "250" in out and "5 个交易日" in out
        assert "--allow-short" in out
        assert "rank" not in out                      # 未产出排序结论

    def test_allow_short_marks_artifact(self, memory_config, tmp_path, capsys):
        """判据 B：显式放行 ⇒ 退出 0；产物内可 grep 到 override 标记。"""
        import json

        import yaml
        from btf.cli.main import main

        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(memory_config, allow_unicode=True),
                        encoding="utf-8")
        out_json = tmp_path / "short.json"
        rc = main(["optimize", "--config", str(path),
                   "--grid", "run.params.weight=0.5,0.9",
                   "--objective", "final_nav", "--workers", "0",
                   "--allow-short", "--out", str(out_json)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "放行" in out
        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert report["short_period_override"] == {
            "n_days": 5, "threshold": 250, "override": True}

    def test_long_period_no_flag_unchanged(self, long_memory_config, tmp_path,
                                           capsys):
        """判据 C：n_days ≥ 阈值 ⇒ 不加开关即通过（行为与改动前一致）。"""
        import json

        import yaml
        from btf.cli.main import main

        path = tmp_path / "long.yaml"
        path.write_text(yaml.safe_dump(long_memory_config, allow_unicode=True),
                        encoding="utf-8")
        out_json = tmp_path / "long.json"
        rc = main(["optimize", "--config", str(path),
                   "--grid", "run.params.weight=0.5,0.9",
                   "--objective", "final_nav", "--workers", "0",
                   "--out", str(out_json)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "grid_search" in out and "2 组" in out
        assert "250" not in out                       # 无阻断文案
        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert report["n_trading_days"] == 260
        assert "short_period_override" not in report

    def test_allow_short_on_long_period_no_marker(self, long_memory_config,
                                                  tmp_path):
        """反向探针：开关在但**未触发** ⇒ 产物仍无 override 标记
        （override 只记"实际放行"，不得恒现）。"""
        import json

        import yaml
        from btf.cli.main import main

        path = tmp_path / "long2.yaml"
        path.write_text(yaml.safe_dump(long_memory_config, allow_unicode=True),
                        encoding="utf-8")
        out_json = tmp_path / "long2.json"
        rc = main(["optimize", "--config", str(path),
                   "--grid", "run.params.weight=0.9",
                   "--objective", "final_nav", "--workers", "0",
                   "--allow-short", "--out", str(out_json)])
        assert rc == 0
        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert "short_period_override" not in report
