# -*- coding: utf-8 -*-
"""L2 集成：报告「假设与披露」读**生效配置**（RC-03，2026-10-05）。

原缺陷：`app/services.assemble_assumptions` 把滑点**硬编码**为
`"none（回测基线）"`、撮合硬编码为 `"next_open（T+1 开盘撮合）"` ——
用户配了 `pct`，报告仍显示 none：**披露与事实相反**（13 §19.4 假设字段
来源纪律：假设类字段一律读 `manifest.config_effective`）。

断言级别：**渲染级**（对生成的 HTML 断言；不只看 Python 对象）。
"""
from __future__ import annotations

import pytest
from btf import registry
from btf.app.services import assemble_assumptions
from btf.domain.contracts import CONTRACT_VERSION
from btf.experiment.store import LocalRunStore
from btf.runtime import BTFRuntime
from btf.viz.report import ReportBuilder, format_matching, format_slippage

from tests.fixtures.scenarios import t_plus1_scenario

pytestmark = [pytest.mark.l2]

FEED_NAME = "rc03_report_feed"


@pytest.fixture()
def feed_registered():
    feed = t_plus1_scenario().feed

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, _factory)
    return feed


def _run(feed_registered, tmp_path, *, execution: dict | None = None):
    """真落盘（LocalRunStore）→ 读回 bundle → 装配假设（CLI/api 同一函数）。"""
    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": "2015-01-05", "end": "2015-01-09"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": FEED_NAME},
        "risk": {"rules": [{"name": "cash_check"}]},
    }
    if execution is not None:
        cfg["execution"] = execution
    store = LocalRunStore(tmp_path)
    rt = BTFRuntime().load_config(cfg).build()
    run_id = rt.run(store=store).run_id
    return store, store.load(run_id)


def _html(store, bundle) -> str:
    return ReportBuilder(bundle, assemble_assumptions(store, bundle)).build()


class TestAssumptionsReadEffectiveConfig:
    """RC-03 渲染级：报告假设字段 == 生效配置。"""

    def test_pct_shown_not_none(self, feed_registered, tmp_path):
        store, bundle = _run(
            feed_registered, tmp_path,
            execution={"slippage": {"model": "pct", "params": {"pct": 0.002}}})
        html = _html(store, bundle)
        assert "pct 0.002（单边 20bp）" in html
        assert "none（回测基线）" not in html          # 硬编码残留不得出现

    def test_explicit_none_shown(self, feed_registered, tmp_path):
        store, bundle = _run(
            feed_registered, tmp_path,
            execution={"slippage": {"model": "none"}})
        html = _html(store, bundle)
        assert "none（显式声明，零摩擦基线）" in html

    def test_default_is_pct_10bp(self, feed_registered, tmp_path):
        """不写 `execution.slippage` → 缺省（RC-01）= pct 0.001（单边 10bp）。"""
        store, bundle = _run(feed_registered, tmp_path)
        html = _html(store, bundle)
        assert "pct 0.001（单边 10bp）" in html

    def test_fixed_model_readable(self, feed_registered, tmp_path):
        store, bundle = _run(
            feed_registered, tmp_path,
            execution={"slippage": {"model": "fixed",
                                    "params": {"amount": 0.02}}})
        html = _html(store, bundle)
        assert "fixed 0.02 元" in html

    def test_matching_rendered_from_config(self, feed_registered, tmp_path):
        store, bundle = _run(feed_registered, tmp_path)
        cfg = bundle.manifest.config_effective
        assert cfg["execution"]["handler"] == "next_open"      # 生效配置真值
        assert format_matching("next_open") in _html(store, bundle)


class TestSlippageCostDisclosureF9:
    """**F9（2026-10-06）**：报告「假设与披露」单列**价内滑点成本**。

    背景：滑点建模在**成交价内**（`fill_price = ref × (1 ± δ)`），**不计入
    `total_fees`** ⇒ `fee_ratio` 系统性低估真实交易成本（19 号 §65.5 实测
    v77 同区间 ≈2.08 倍）。本批让报告由 `fills.jsonl::ref_price` 还原并单列。
    """

    @staticmethod
    def _impact(fills) -> tuple[float, float]:
        """**独立复算**（不复用被测实现）：Σ qty×|price−ref| 与 Σ fee.total。"""
        impact = fees = 0.0
        for f in fills:
            ref = f.get("ref_price")
            assert ref is not None, "新产物必须记录 ref_price"
            delta = float(f["price"]) - float(ref)
            if f["side"] == "sell":
                delta = -delta
            impact += int(f["qty"]) * delta
            fees += float(f["fee"]["total"])
        return impact, fees

    def test_pct_run_discloses_impact_cost(self, feed_registered, tmp_path):
        store, bundle = _run(feed_registered, tmp_path, execution={
            "handler": "next_open", "cost_model": "flat_rate",
            "rebalancer": "full",
            "slippage": {"model": "pct", "params": {"pct": 0.002}}})
        html = _html(store, bundle)
        assert "滑点成本（价内）" in html
        impact, fees = self._impact(bundle.fills)
        assert impact > 0 and fees > 0
        assert f"≈ {impact:,.2f} 元" in html                       # 金额逐位可核
        assert f"显性费用 {fees:,.2f} 元的 {impact / fees:.2f} 倍" in html

    def test_none_run_discloses_zero_friction(self, feed_registered, tmp_path):
        store, bundle = _run(feed_registered, tmp_path, execution={
            "handler": "next_open", "cost_model": "flat_rate",
            "rebalancer": "full",
            "slippage": {"model": "none"},
            "handler_params": {"volume_participation": None}})
        html = _html(store, bundle)
        assert "滑点成本（价内）" in html
        assert "零摩擦（滑点模型 none，显式声明）" in html
        impact, _ = self._impact(bundle.fills)
        assert impact == 0.0

    def test_old_artifacts_without_ref_price_report_unavailable(
            self, feed_registered, tmp_path):
        """**诚实边界**：旧产物（无 `ref_price`）⇒ 明示「不可得」，**不得静默算 0**。"""
        from dataclasses import replace

        store, bundle = _run(feed_registered, tmp_path, execution={
            "slippage": {"model": "pct", "params": {"pct": 0.002}}})
        legacy = [{k: v for k, v in f.items() if k != "ref_price"}
                  for f in bundle.fills]
        assert legacy and all("ref_price" not in f for f in legacy)
        old = replace(bundle, fills=legacy)          # 模拟 RC 之前的产物
        assert "不可得（产物未记录 ref_price——重跑后可披露）" in _html(store, old)


class TestMatchingNotHardcoded:
    """结构级：`assemble_assumptions` 不得写死 `next_open`（防硬编码回归）。"""

    def _assemble(self, *, execution: dict | None = None):
        from types import SimpleNamespace

        manifest = SimpleNamespace(
            run_id="20261005_000000_abcdef",
            rules_version="v7.7@sha256:deadbeef",
            rules_overrides=(),
            config_effective={"execution": execution or {"handler": "my_open"}},
        )
        store = SimpleNamespace(events_sampled=lambda run_id: False)
        return assemble_assumptions(store, SimpleNamespace(manifest=manifest))

    def test_matching_follows_config(self):
        assert self._assemble().matching == "my_open"
        assert "next_open" not in self._assemble().matching

    def test_slippage_missing_in_config_falls_back_to_none_label(self):
        """旧产物（config_effective 无 `slippage` 键）→ 兜底串仍可读、不崩。"""
        assert self._assemble().slippage == "none（显式声明，零摩擦基线）"


class TestBenchmarkSoftMandateRc09:
    """RC-09（2026-10-05）：基准软强制——不配 `report.benchmark` **不阻断**，
    但终端 + 报告两处必须显式标注「无基准对照」（双向探针防"恒现/恒空"）。"""

    def test_report_discloses_no_benchmark(self, feed_registered, tmp_path):
        """报告「假设与披露」出现无基准标注（渲染级）。"""
        store, bundle = _run(feed_registered, tmp_path)
        html = _html(store, bundle)
        assert "无基准对照" in html
        assert "report.benchmark" in html

    def test_benchmark_configured_no_such_disclosure(self):
        """双向探针：配了基准 ⇒ 走「基准对比」分支、**不出现**无基准标注。"""
        from types import SimpleNamespace

        from btf.viz.report import _assembly_disclosures

        manifest = SimpleNamespace(
            config_effective={"report": {"benchmark": "000300.SH"}},
            assembly_notes=())
        text = " ".join(_assembly_disclosures(manifest))
        assert "000300.SH" in text
        assert "无基准对照" not in text

    def test_cli_run_warns_without_benchmark(self, feed_registered, tmp_path,
                                             capsys):
        """真入口：不配 benchmark 跑一次 ⇒ 终端 warn 出现，且**不阻断**（exit 0）。"""
        import yaml
        from btf.cli.main import main

        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "000001.SZ", "weight": 0.9},
                "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
                "period": {"start": "2015-01-05", "end": "2015-01-09"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": FEED_NAME},
            "risk": {"rules": [{"name": "cash_check"}]},
        }
        path = tmp_path / "bt.yaml"
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True),
                        encoding="utf-8")
        assert main(["run", "--config", str(path),
                     "--out", str(tmp_path / "runs")]) == 0
        out = capsys.readouterr().out
        assert "无基准对照" in out and "report.benchmark" in out


class TestFormatHelpers:
    """格式化函数（纯函数，与 `Assumptions` 同源）分支覆盖。"""

    def test_branches(self):
        assert format_slippage("none") == "none（显式声明，零摩擦基线）"
        assert format_slippage("pct", {"pct": 0.001}) == "pct 0.001（单边 10bp）"
        assert format_slippage("pct", {"pct": 0.0015}) == "pct 0.0015（单边 15bp）"
        assert format_slippage("pct", {}) == "pct 0.001（单边 10bp）"   # 模型自身默认
        assert format_slippage("fixed", {"amount": 0.02}) == "fixed 0.02 元"
        assert format_slippage("fixed") == "fixed 0.01 元"
        assert format_slippage("weird", {"k": 1}) == 'weird {"k": 1}'
        assert format_slippage("weird") == "weird"
        assert format_matching("next_open") == "next_open（T+1 开盘撮合）"
        assert format_matching("custom_handler") == "custom_handler"
