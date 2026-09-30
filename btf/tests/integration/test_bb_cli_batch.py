# -*- coding: utf-8 -*-
"""L4 集成：裁决四/六派单 BB-2 / BB-3 / BB-4（19 号 §34.3 / §39.4）。

- **BB-2**：CLI 终端与 `metrics.json` **落盘一致**（不可算指标写 `null`，
  不再打印 Python `nan`）——断言落**展示级**（铁律新 14）。
- **BB-3**：`config-check --resolve-strategy` 暴露"策略类不可加载"（拼写错在
  最低成本环节暴露）；**不带开关时行为不变**（向后兼容）。
- **BB-4**：极短区间（< 20 交易日）**告警但不阻断**；正常区间无告警。
- **连带（BB-1 实测发现）**：「复现说明」为**强制章节**——`report.sections`
  收窄时不得丢掉它（否则数据指纹/摘要无处可见，铁律新 16）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from btf import registry
from btf.config.loader import DEFAULTS
from btf.domain.contracts import CONTRACT_VERSION

pytestmark = [pytest.mark.l4]

FEED_NAME = "bb_cli_feed"
DAYS = ("2015-01-05", "2015-01-06", "2015-01-07", "2015-01-08", "2015-01-09")

#: 长区间数据源（BB-4「正常区间无告警」用）：25 个连续工作日
LONG_FEED_NAME = "bb_cli_long_feed"
LONG_DAYS: tuple[str, ...] = ()


def _build_long_days() -> tuple[str, ...]:
    from datetime import date, timedelta

    out: list[str] = []
    day = date(2015, 1, 5)
    while len(out) < 25:
        if day.weekday() < 5:
            out.append(day.strftime("%Y-%m-%d"))
        day += timedelta(days=1)
    return tuple(out)


@pytest.fixture()
def _long_feed():
    """25 交易日合成数据源（注册进 registry，供 CLI 真入口使用）。"""
    from btf.data.memory import MemoryFeed
    from btf.domain.types import AssetClass, Instrument, TradingDate

    global LONG_DAYS
    if not LONG_DAYS:
        LONG_DAYS = _build_long_days()
    from tests.fixtures.scenarios import make_bar, make_state

    days = [TradingDate.from_ymd(d.replace("-", "")) for d in LONG_DAYS]
    bars, states, px = [], {}, 10.0
    for i, d in enumerate(days):
        pre = px
        px = round(px * (1.0 + (0.004 if i % 3 else -0.003)), 4)
        bars.append(make_bar("000001.SZ", d, px, px, px, px, pre))
        states[(d.to_ymd(), "000001.SZ")] = make_state(
            "000001.SZ", d, pre=pre if i else 10.0)
    instruments = {"000001.SZ": Instrument(
        symbol="000001.SZ", asset_class=AssetClass.STOCK, board="main",
        lot_size=100)}
    feed = MemoryFeed(bars, states, instruments, dates=days)

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if LONG_FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, LONG_FEED_NAME, _factory)
    return feed


@pytest.fixture()
def _feed_registered():
    from tests.fixtures.scenarios import t_plus1_scenario

    feed = t_plus1_scenario().feed

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, _factory)
    return feed


def _config(tmp_path: Path, *, start: str, end: str,
            sections: list[str] | None = None) -> Path:
    import yaml

    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": start, "end": end},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": FEED_NAME},
        "risk": {"rules": [], "allow_empty_chain": True},   # X-7 逃生声明
    }
    if sections is not None:
        cfg["report"] = {"sections": sections}
    path = tmp_path / "bb.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


class TestBB2TerminalMatchesDisk:
    """BB-2：展示层 == 落盘（null），不再出现 Python `nan`。"""

    def test_single_day_run_prints_null(self, _feed_registered, tmp_path,
                                        capsys):
        from btf.cli.main import main

        cfg = _config(tmp_path, start=DAYS[0], end=DAYS[0])     # 1 交易日
        out_dir = tmp_path / "runs"
        assert main(["run", "--config", str(cfg), "--out", str(out_dir)]) == 0
        out = capsys.readouterr().out
        assert "nan" not in out, "终端仍打印 Python nan（BB-2 回归）"
        assert "sharpe_ratio: null" in out, out
        assert "annualized_return: null" in out, out

        run_id = sorted(p.name for p in out_dir.iterdir() if p.is_dir())[0]
        metrics = json.loads((out_dir / run_id / "metrics.json").read_text(
            encoding="utf-8"))["metrics"]
        assert metrics["sharpe_ratio"] is None       # 落盘 null（与终端一致）
        assert metrics["annualized_return"] is None

    def test_fmt_metric_three_states(self):
        from btf.cli.main import _fmt_metric

        assert _fmt_metric(None) == "null"
        assert _fmt_metric(float("nan")) == "null"
        assert _fmt_metric(0.25) == "0.25"
        assert _fmt_metric(0) == "0"


class TestBB3ResolveStrategy:
    """BB-3：`config-check --resolve-strategy`（默认关闭，行为向后兼容）。"""

    def test_bad_class_name_exits_one(self, _feed_registered, tmp_path, capsys):
        from btf.cli.main import main

        cfg_path = _config(tmp_path, start=DAYS[0], end=DAYS[-1])
        text = cfg_path.read_text(encoding="utf-8").replace(
            "tests.fixtures.buyhold:ConfigBuyHold",
            "tests.fixtures.buyhold:ConfigBuyHoldX")
        cfg_path.write_text(text, encoding="utf-8")

        assert main(["config-check", str(cfg_path)]) == 0        # 默认不检查
        capsys.readouterr()
        assert main(["config-check", str(cfg_path),
                     "--resolve-strategy"]) == 1
        err = capsys.readouterr().err
        assert "no attribute" in err or "无属性" in err, err

    def test_good_class_reports_loadable(self, _feed_registered, tmp_path,
                                        capsys):
        from btf.cli.main import main

        cfg_path = _config(tmp_path, start=DAYS[0], end=DAYS[-1])
        assert main(["config-check", str(cfg_path),
                     "--resolve-strategy"]) == 0
        out = capsys.readouterr().out
        assert "策略可加载" in out

    def test_missing_module_reports_path_hint(self, _feed_registered,
                                              tmp_path, capsys):
        from btf.cli.main import main

        cfg_path = _config(tmp_path, start=DAYS[0], end=DAYS[-1])
        text = cfg_path.read_text(encoding="utf-8").replace(
            "tests.fixtures.buyhold:ConfigBuyHold", "no_such_pkg.strat:Strat")
        cfg_path.write_text(text, encoding="utf-8")
        assert main(["config-check", str(cfg_path),
                     "--resolve-strategy"]) == 1
        err = capsys.readouterr().err
        assert "PYTHONPATH" in err, err


class TestBB4ShortPeriodWarning:
    """BB-4：极短区间告警**不阻断**；正常区间无告警。"""

    def test_short_period_warns_but_succeeds(self, _feed_registered, tmp_path,
                                             capsys):
        from btf.cli.main import main

        cfg = _config(tmp_path, start=DAYS[0], end=DAYS[2])      # 3 交易日
        assert main(["run", "--config", str(cfg),
                     "--out", str(tmp_path / "runs")]) == 0
        out = capsys.readouterr().out
        assert "[warn] 区间仅 3 个交易日" in out, out
        assert "optimize --objective" in out, "告警须说明防污染用途"

    def test_normal_period_no_warning(self, _long_feed, tmp_path, capsys):
        """≥20 交易日：CLI **不得**打印告警（真入口，25 日合成数据源）。"""
        import yaml
        from btf.cli.main import main
        from btf.config.paths import RUNS_DIR  # noqa: F401  (占位：不改默认库)

        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "000001.SZ", "weight": 0.9},
                "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
                "period": {"start": LONG_DAYS[0], "end": LONG_DAYS[-1]},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": LONG_FEED_NAME},
            "risk": {"rules": [], "allow_empty_chain": True},
        }
        path = tmp_path / "long.yaml"
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        assert main(["run", "--config", str(path),
                     "--out", str(tmp_path / "runs")]) == 0
        out = capsys.readouterr().out
        assert "[warn] 区间仅" not in out, out

    def test_threshold_boundary_unit(self, caplog):
        """边界（单元级）：25 日无告警；3 日告警（日志通道）。"""
        from btf.runtime import BTFRuntime

        class _R:
            def __init__(self, n): self.n_days = n

        with caplog.at_level("WARNING"):
            BTFRuntime._warn_if_short_period(_R(25))
        assert not [r for r in caplog.records if "极短区间" in r.message]
        caplog.clear()
        with caplog.at_level("WARNING"):
            BTFRuntime._warn_if_short_period(_R(3))
        assert [r for r in caplog.records if "极短区间" in r.message]


class TestMandatoryReproduceSection:
    """连带修复（BB-1 实测发现）：复现说明为**强制章节**，且默认全章。"""

    def test_defaults_cover_all_sections(self):
        assert {"reproduce", "appendix"} <= set(DEFAULTS["report"]["sections"])

    def test_narrowed_sections_keep_reproduce_and_fingerprint(
            self, _feed_registered, tmp_path):
        """`report.sections=[summary]` 时仍须保留复现说明（数据指纹可见）。"""
        from btf.cli.main import main

        cfg = _config(tmp_path, start=DAYS[0], end=DAYS[-1],
                      sections=["summary"])
        out_dir = tmp_path / "runs"
        assert main(["run", "--config", str(cfg), "--out", str(out_dir)]) == 0
        run_id = sorted(p.name for p in out_dir.iterdir() if p.is_dir())[0]
        assert main(["report", "--run", run_id, "--out-dir", str(out_dir)]) == 0
        html = (out_dir / run_id / "report.html").read_text(encoding="utf-8")
        assert 'id="reproduce"' in html, "复现说明被 sections 关掉（强制章节失效）"
        assert "数据指纹" in html
        # 渲染级断言（铁律新 14）
        assert "<table><thead>" in html
        assert "&lt;div class=" not in html
