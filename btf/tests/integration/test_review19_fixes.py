# -*- coding: utf-8 -*-
"""L2/L4 集成：19 号架构审查修正验收（v0.5.1）。

覆盖（逐项对应报告 §4 问题清单与 §15 验证指标）：
    P0-3  风控 fail-closed：空链默认 ConfigError + 逃生开关 + 报告披露
    P1-2  策略版本协商：未声明/主版本不符 → ContractVersionError 拒载
    P1-5  `missing_bars_action` 死键删除（schema 拒绝 + DEFAULTS 无该键）
    P1-6  成本模型显式化：zero 披露进报告假设章节
    P1-7  `analysis` 段可配置（risk_free_rate / annualization_factor）
    P1-10 基准对比：`report.benchmark` 死键转活 + 相对指标 ≥6 项
    P1-11 区间守卫：区间早于 stk_limit 起点 → ConfigError（可显式降级）
    EX-1  三扩展点接线：自定义 Analyzer/RulesProvider 注册后零源码改动生效
    P2-9  缺口披露对称（股票限价缺失与 ETF 回退同通道）
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
import yaml

pytestmark = [pytest.mark.l2]

from btf import registry  # noqa: E402
from btf.config.validation import ConfigError  # noqa: E402
from btf.domain.contracts import CONTRACT_VERSION  # noqa: E402

FEED_NAME = "review19_memory_feed"


def _feed_factory(**_params):
    from tests.fixtures.scenarios import t_plus1_scenario

    feed = t_plus1_scenario().feed
    return feed


def _base_config(**overrides) -> dict:
    factory = _feed_factory
    factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, factory)
    cfg: dict = {
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
    cfg.update(overrides)
    return cfg


class TestRiskFailClosed:
    """P0-3：空风控链不再静默裸奔。"""

    def test_empty_chain_hard_rejected_when_disabled(self):
        """`allow_empty_chain: false` → 硬拒绝（Q1 保守 B 方案的可配置硬模式）。"""
        from btf.runtime import BTFRuntime

        cfg = _base_config()
        cfg["risk"] = {"rules": [], "allow_empty_chain": False}
        rt = BTFRuntime().load_config(cfg)
        with pytest.raises(ConfigError, match=r"risk\.rules 为空"):
            rt.build()

    def test_explicit_allow_empty_chain_passes_and_discloses(self):
        """逃生开关放行 + manifest 配置回显 + 报告披露（不再静默）。"""
        from btf.runtime import BTFRuntime

        cfg = _base_config()
        cfg["risk"] = {"rules": [], "allow_empty_chain": True}
        cfg["execution"] = {"handler": "next_open", "cost_model": "zero",
                            "rebalancer": "full"}
        rt = BTFRuntime().load_config(cfg).build()
        assert rt.risk_allow_empty is True
        result = rt.run(persist=False)

        from btf.viz.report import Assumptions, ReportBuilder, _derived_disclosures

        # 直接验证派生披露纯函数（不依赖落盘）
        class _M:
            config_effective: ClassVar[dict] = {
                "risk": {"allow_empty_chain": True},
                "execution": {"cost_model": "zero"}}
        notes = _derived_disclosures(_M())
        assert any("风控链为空" in n for n in notes)
        assert any("零费用基线" in n for n in notes)
        assert Assumptions is not None and ReportBuilder is not None
        assert result.n_days > 0

    def test_strict_rule_rejects_without_price(self):
        """strict=True：无盯市价 → RISK_REJECTED 而非放行（P0-3 规则层）。"""
        from btf.risk.rules import MaxWeightRule

        rule = MaxWeightRule(strict=True)

        class _Portfolio:
            positions: ClassVar[dict] = {}

            def close_of(self, symbol):      # 无价
                return None

            def total_value(self):
                return 1.0

        class _Order:
            symbol = "000001.SZ"
            side = type("S", (), {"value": "buy"})()

        from btf.domain.orders import OrderSide

        _Order.side = OrderSide.BUY
        strict_verdict = rule.check(_Order(), _Portfolio(), {})
        lenient_verdict = MaxWeightRule(strict=False).check(
            _Order(), _Portfolio(), {})
        assert repr(strict_verdict) != repr(lenient_verdict), (
            "strict 与 lenient 裁决应不同（无价订单：拒单 vs 放行）")
        assert "strict" in repr(strict_verdict)


class TestStrategyVersionNegotiation:
    """P1-2：策略参与 contract_version 协商（原 importlib 直载不校验）。"""

    def test_undeclared_strategy_rejected(self):
        from btf.runtime import BTFRuntime

        cfg = _base_config()
        cfg["run"]["strategy"] = "btf.strategy.base:StrategyBase"
        rt = BTFRuntime().load_config(cfg)
        with pytest.raises(Exception, match=r"contract_version|主版本"):
            rt.build()

    def test_declared_strategy_passes(self):
        from btf.runtime import BTFRuntime

        cfg = _base_config()
        cfg["risk"] = {"rules": [], "allow_empty_chain": True}
        rt = BTFRuntime().load_config(cfg).build()
        assert rt.strategy is not None


class TestDeadKeysRemoved:
    """P1-5：`missing_bars_action` 死键删除（schema + DEFAULTS 同步）。"""

    def test_schema_rejects_completeness(self):
        from btf.config.loader import load_config
        from btf.config.validation import validate

        cfg = _base_config()
        cfg["data"] = {"feed": FEED_NAME,
                       "completeness": {"missing_bars_action": "fail"}}
        merged = load_config(cfg)
        with pytest.raises(ConfigError, match="completeness"):
            validate(merged)

    def test_defaults_have_no_dead_key(self):
        from btf.config.loader import DEFAULTS

        assert "completeness" not in DEFAULTS.get("data", {})


class TestRiskFailClosedDefault:
    """X-7（19 号 §18.1 裁决 1 / Q1=A）：风控**默认 fail-closed**。

    v0.5.1 落地的是 B 方案（默认层 `allow_empty_chain=True` → 默认放行），
    与 CHANGELOG「fail-closed」措辞矛盾（§17.4.2）——本次按裁决改 A：
    默认层不声明逃生开关 → 空链装配期 ConfigError；显式 `true` 才放行。
    """

    def test_defaults_do_not_opt_out(self):
        from btf.config.loader import DEFAULTS

        assert "allow_empty_chain" not in DEFAULTS["risk"], (
            "默认层不得声明逃生开关（否则空链默认放行 = fail-open）")

    def test_empty_chain_rejected_by_default(self):
        from btf.config.validation import ConfigError
        from btf.runtime import BTFRuntime

        cfg = _base_config()
        cfg.pop("risk", None)                 # 不声明 → 默认拒绝
        rt = BTFRuntime().load_config(cfg)
        with pytest.raises(ConfigError, match=r"fail-closed|风控"):
            rt.build()

    def test_explicit_opt_out_still_allowed(self):
        from btf.runtime import BTFRuntime

        cfg = _base_config()
        cfg["risk"] = {"rules": [], "allow_empty_chain": True}
        rt = BTFRuntime().load_config(cfg).build()
        assert rt.risk_allow_empty is True

    def test_rule_strict_default_is_closed(self):
        """三规则 `strict` 默认 True（缺数据拒单，不再降级放行）。"""
        from btf.risk.rules import (
            CashCheckRule,
            MaxWeightRule,
            TradabilityRule,
        )

        assert MaxWeightRule().strict is True
        assert CashCheckRule().strict is True
        assert TradabilityRule().strict is True


class TestCoverageGuard:
    """P1-11：区间覆盖度守卫（2008 前涨跌停静默失效 → 默认拒绝）。"""

    def test_pre_2008_period_rejected(self):
        from btf.runtime import BTFRuntime

        cfg = _base_config()
        cfg["run"]["period"] = {"start": "2005-06-01", "end": "2005-12-31"}
        rt = BTFRuntime().load_config(cfg)
        with pytest.raises(ConfigError, match=r"stk_limit|覆盖度"):
            rt.build()

    def test_explicit_degraded_allowed(self):
        from btf.runtime import BTFRuntime

        cfg = _base_config()
        cfg["run"]["period"] = {"start": "2005-06-01", "end": "2005-12-31"}
        cfg["data"] = {"feed": FEED_NAME, "allow_degraded_limit": True}
        cfg["risk"] = {"rules": [], "allow_empty_chain": True}
        rt = BTFRuntime().load_config(cfg).build()
        assert rt.coverage is not None and rt.coverage.fatal

    def test_reversed_period_rejected(self):
        from btf.data.coverage import check_coverage

        report = check_coverage("20200101", "20190101")
        assert report.fatal and "倒置" in report.fatal[0]

    def test_etf_limit_is_degraded_not_fatal(self):
        from btf.data.coverage import check_coverage

        report = check_coverage("20100101", "20200101")
        assert not report.fatal
        assert any("etf_limit" in note for note in report.degraded)


class TestBenchmarkLayer:
    """P1-10：基准对比（死键转活 + 相对指标口径）。"""

    def test_relative_metrics_six_plus(self):
        from btf.analytics.benchmark import relative_metrics

        strategy = [1.0, 1.02, 1.01, 1.05, 1.08]
        bench = [1.0, 1.01, 1.00, 1.02, 1.03]
        metrics = relative_metrics(strategy, bench, symbol="000300.SH",
                                   risk_free_rate=0.0)
        required = ("benchmark_total_return", "excess_return",
                    "annualized_excess", "tracking_error",
                    "information_ratio", "beta", "alpha_annualized",
                    "up_capture", "down_capture")
        for key in required:
            assert key in metrics, key
        assert metrics["excess_return"] == pytest.approx(1.08 - 1.03)
        assert metrics["benchmark_symbol"] == "000300.SH"

    def test_benchmark_requires_alignment(self):
        from btf.analytics.benchmark import BenchmarkError, relative_metrics

        with pytest.raises(BenchmarkError, match="长度"):
            relative_metrics([1.0, 1.1], [1.0, 1.0, 1.0])

    def test_index_series_fail_closed_on_missing_day(self):
        from btf.data.index_series import IndexSeriesError, load_index_closes

        with pytest.raises(IndexSeriesError, match="无收盘价"):
            load_index_closes("000300.SH", "20200101", "20200110",
                              trading_days=["18000101"])     # 不可能交易日


class TestExtensionPointsLive:
    """EX-1：注册后零源码改动生效（三个被绕过的扩展点归位）。"""

    def test_custom_analyzer_via_config(self):
        from btf.analytics.metrics import MetricBase
        from btf.runtime import BTFRuntime

        name = "review19_custom_metric"

        class _Custom(MetricBase):       # type: ignore[misc]
            name: ClassVar[str] = "review19_custom_metric"

            def _value(self, series, cfg):
                return 0.42

        if name not in registry.available(registry.ANALYZER):
            registry.register(registry.ANALYZER, name, _Custom)
        cfg = _base_config()
        cfg["risk"] = {"rules": [], "allow_empty_chain": True}
        cfg["analysis"] = {"metrics": ["final_nav", name]}
        rt = BTFRuntime().load_config(cfg).build()
        result = rt.run(persist=False)
        # 经 registry 解析的指标集（不落盘时看 run 结果 metrics 需落盘；
        # 这里直接验证解析器链路：名字表 → resolve → 自定义类）
        resolved = registry.resolve(registry.ANALYZER, name)
        assert resolved is _Custom
        from btf.analytics.metrics import compute_all

        values = compute_all([], [], {}, analyzer_names=["final_nav", name],
                             resolver=lambda n: registry.resolve(
                                 registry.ANALYZER, n))
        assert values[name] == pytest.approx(0.42)
        assert rt.strategy is not None and result is not None

    def test_unknown_analyzer_rejected(self):
        """未登记名字经 registry 解析 → 显式报错（不静默跳过）。"""
        cfg = _base_config()
        cfg["risk"] = {"rules": [], "allow_empty_chain": True}
        cfg["analysis"] = {"metrics": ["no_such_metric"]}
        from btf.runtime import BTFRuntime

        rt = BTFRuntime().load_config(cfg).build()
        assert rt._analyzer_names() == ["no_such_metric"]
        with pytest.raises(Exception, match=r"no_such_metric|未登记|未知"):
            registry.resolve(registry.ANALYZER, "no_such_metric")

    def test_custom_rules_provider_via_registry(self):
        """自定义 RulesProvider 注册后经 rules.source 使用（零源码改动）。"""
        from btf.runtime import BTFRuntime
        from btf.strategy.rules import StaticRulesProvider

        def factory(cfg=None, **_params):
            return StaticRulesProvider(params={}, version="review19")

        factory.contract_version = CONTRACT_VERSION    # S1 协商（EX-1 要求）

        if "review19_custom" not in registry.available(registry.RULES_PROVIDER):
            registry.register(registry.RULES_PROVIDER, "review19_custom", factory)
        cfg = _base_config()
        cfg["risk"] = {"rules": [], "allow_empty_chain": True}
        cfg["rules"] = {"source": "review19_custom"}
        rt = BTFRuntime().load_config(cfg).build()
        assert rt.rules_provider.rules_version() == "review19"


class TestDisclosureSymmetry:
    """P2-9：股票限价缺失与 ETF 回退走同一披露通道。"""

    def test_stock_missing_limit_registered(self):
        from btf.data.state import StateSynthesizer
        from btf.domain.types import TradingDate

        synth = StateSynthesizer()
        synth.states(TradingDate.from_ymd("20050601"), ["600000.SH"],
                     with_touch_flags=False)
        notes = synth.degraded_notes()
        assert any("股票涨跌停缺失" in n for n in notes), notes

    def test_recent_period_has_no_stock_note(self, tmp_path):
        from btf.data.state import StateSynthesizer
        from btf.domain.types import TradingDate

        synth = StateSynthesizer()
        synth.states(TradingDate.from_ymd("20250901"), ["600000.SH"],
                     with_touch_flags=False)
        assert not any("股票涨跌停缺失" in n for n in synth.degraded_notes())


class TestPyprojectVersionSingleSource:
    """R0-3/P3-4：版本号单源（pyproject dynamic ← btf._version）。"""

    def test_dynamic_version_declared(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
        assert 'dynamic = ["version"]' in text
        assert 'version = {attr = "btf._version.__version__"}' in text
        assert '\nversion = "0.2.0"' not in text      # 旧双源已除

    def test_runtime_version_matches(self):
        """运行时版本 == 单一真源 `btf._version`（**不写死字面量**）。

        原断言写死 `"0.5.1"`：每次发版都要同步改测试，且改错即假绿（0.5.2
        发版时才发现）。改为对单一真源取齐——包根 `btf.__version__` 由
        `btf._version` re-export，二者不等即双源漂移回归（P3-4 本义）。
        """
        import re

        import btf
        from btf._version import __version__ as source

        assert btf.__version__ == source
        assert re.fullmatch(r"\d+\.\d+\.\d+", source), (
            f"版本号形制应为 语义化版本 x.y.z：{source!r}")


def test_yaml_roundtrip_of_new_sections(tmp_path):
    """新配置段（analysis/risk.allow_empty_chain）经 YAML 合法（schema 同步）。"""
    cfg = _base_config()
    cfg["risk"] = {"rules": [], "allow_empty_chain": True}
    cfg["analysis"] = {"risk_free_rate": 0.02, "annualization_factor": 244,
                       "metrics": ["final_nav"]}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    from btf.config.loader import load_config
    from btf.config.validation import validate

    validate(load_config(path))
