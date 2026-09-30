# -*- coding: utf-8 -*-
"""可视化与报告单测（M2 任务 5.8；13 §19.2/19.4/19.5/19.6）。

验收：
    1. ChartSpec/ChartQuery 契约（可序列化、契约版本）；
    2. 六图插件产出 Spec（空数据 → 显式报错，由编排层降级）；
    3. 渲染：Plotly 后端 + 降级回退（内置零依赖 HTML）；
    4. **假设与披露为强制章节**（关闭 → ReportError；报告必含该章节）；
    5. **报告可再生成幂等**（同输入 → 逐字节一致 HTML）；
    6. 图表缺失降级（不中断报告）。
"""
from __future__ import annotations

import pytest
from btf.experiment.manifest import RunManifest
from btf.experiment.store import RunBundle, SnapshotRecord
from btf.viz import charts as vcharts
from btf.viz import render
from btf.viz.contracts import (
    CHART_CONTRACT_VERSION,
    AxisSpec,
    ChartQuery,
    ChartSpec,
    SeriesSpec,
)
from btf.viz.report import Assumptions, ReportBuilder, ReportError

pytestmark = [pytest.mark.l1]

CONFIG = {"run": {"period": {"start": "2015-01-05", "end": "2015-01-09"}},
          "data": {"feed": "memory", "token": "SECRET"}}

METRICS = {
    "final_nav": 1_010_000.0, "total_return": 0.01,
    "annualized_return": 0.5, "sharpe_ratio": 1.2, "max_drawdown": -0.02,
    "n_fills": 2.0, "total_turnover": 20_000.0, "total_fees": 11.0,
    "turnover_annualized": 1.95, "fee_ratio": 0.001,
}


def _snapshots() -> list[SnapshotRecord]:
    navs = [1_000_000.0, 1_010_000.0, 1_005_000.0, 1_010_000.0]
    peak, out = 0.0, []
    dates = ["20150105", "20150106", "20150107", "20150108"]
    for i, (date, tv) in enumerate(zip(dates, navs, strict=True)):
        peak = max(peak, tv)
        out.append(SnapshotRecord(
            date=date, cash=10_000.0, market_value=tv - 10_000.0, total_value=tv,
            daily_return=0.0 if i == 0 else tv / navs[i - 1] - 1.0,
            cumulative_return=tv / navs[0] - 1.0, drawdown=tv / peak - 1.0,
            weights={"000001.SZ": 0.9, "@CASH": 0.1},
            positions_qty={"000001.SZ": 900}))
    return out


def _bundle(with_trades: bool = True) -> RunBundle:
    trades = [{
        "trade_id": "T000001", "symbol": "000001.SZ", "qty": 900, "pnl": 12.5,
        "holding_days": 3, "tag": None,
        "open_fill": {"fill_id": "F1", "order_id": "O1", "symbol": "000001.SZ",
                      "side": "buy", "qty": 900, "price": 10.0,
                      "fee": {"commission": 5.0, "stamp_duty": 0.0,
                              "transfer_fee": 0.02, "total": 5.02},
                      "fill_date": "20150105", "fill_timing": "open"},
        "close_fill": None,
    }] if with_trades else []
    return RunBundle(
        manifest=RunManifest.skeleton(CONFIG, run_id="test_run_000000",
                                      rules_version="v7.7@sha256:ab12"),
        metrics=dict(METRICS), snapshots=_snapshots(), trades=trades,
        fills=[{"fill_id": "F1", "symbol": "000001.SZ", "side": "buy",
                "qty": 900, "price": 10.0,
                "fee": {"commission": 5.0, "stamp_duty": 0.0,
                        "transfer_fee": 0.02, "total": 5.02},
                "fill_date": "20150105"}],
        rejections=[{"code": "insufficient_cash", "message": "现金不足"}],
    )


def _assumptions() -> Assumptions:
    return Assumptions(
        matching="next_open", slippage="none",
        fee_segments=(
            {"effective_from": None, "stamp_duty_sell": 0.001,
             "transfer_fee_rate": 0.0006, "transfer_scope": "sh_only",
             "transfer_basis": "par"},
            {"effective_from": "20230828", "stamp_duty_sell": 0.0005,
             "transfer_fee_rate": 0.00001, "transfer_scope": "both",
             "transfer_basis": "amount"},
        ),
        rules_version="v7.7@sha256:ab12",
        rules_overrides=({"rule_id": "L2", "value": 20},),
        degraded=("事件日志已采样（>50 万事件）",),
        notes=("2020+ 库内 pay_date ≡ ex_date",),
    )


# ═════════════════════════════════════════════════════════════
class TestContracts:
    def test_query_contract_version(self):
        q = ChartQuery(run_id="r1", dataset="snapshots",
                       fields=["date", "total_value"])
        assert q.contract_version == CHART_CONTRACT_VERSION

    def test_spec_serializable(self):
        spec = ChartSpec(
            chart_type="line", title="t", subtitle=None,
            series=[SeriesSpec(name="nav", values=[1.0, 2.0])],
            axes={"y": AxisSpec(title="元")})
        as_dict = spec.to_dict()
        assert as_dict["chart_type"] == "line"
        assert as_dict["series"][0]["values"] == [1.0, 2.0]


class TestChartPlugins:
    @pytest.mark.parametrize("name", ["equity", "drawdown", "monthly_heatmap",
                                      "trades_table", "metrics_table",
                                      "weights_area"])
    def test_six_charts_build(self, name):
        bundle = _bundle()
        spec = vcharts.build_chart(name, {
            "snapshots": bundle.snapshots, "trades": bundle.trades,
            "fills": bundle.fills, "rejections": bundle.rejections,
            "metrics": bundle.metrics})
        assert spec.title and spec.series

    def test_chart_names_registered(self):
        assert set(vcharts.CHART_PLUGINS) == set(vcharts.DEFAULT_CHARTS)

    @pytest.mark.parametrize("name", ["equity", "drawdown", "monthly_heatmap",
                                      "weights_area"])
    def test_empty_snapshots_raises_for_degradation(self, name):
        """空数据 → 显式报错（由编排层捕获并标注"图表缺失"）。"""
        with pytest.raises(ValueError):
            vcharts.build_chart(name, {"snapshots": []})

    def test_monthly_heatmap_aggregates(self):
        spec = vcharts.build_chart("monthly_heatmap",
                                   {"snapshots": _snapshots()})
        rows = list(spec.series[0].values)
        assert len(rows) == 1 and len(rows[0]) == 12      # 1 年 × 12 月

    def test_weights_area_includes_cash(self):
        spec = vcharts.build_chart("weights_area", {"snapshots": _snapshots()})
        names = [s.name for s in spec.series]
        assert "000001.SZ" in names and "@CASH" in names


class TestRenderer:
    def test_plotly_backend_renders_html(self):
        spec = vcharts.build_chart("equity", {"snapshots": _snapshots()})
        html = render.render_html(spec, include_plotlyjs=False)
        assert "<div" in html and "plotly" in html.lower()

    def test_table_rendered(self):
        spec = vcharts.build_chart("metrics_table", {"metrics": METRICS})
        assert "final_nav" in render.render_html(spec)

    def test_fallback_without_plotly(self, monkeypatch):
        """plotly 不可用 → 内置零依赖渲染（不中断），内容仍可读。"""
        monkeypatch.setattr(render, "_PLOTLY", False)
        spec = vcharts.build_chart("equity", {"snapshots": _snapshots()})
        html = render.render_html(spec)
        assert ("chart" in html and "降级" in html) or "<svg" in html

    def test_fallback_table(self, monkeypatch):
        monkeypatch.setattr(render, "_PLOTLY", False)
        spec = vcharts.build_chart("metrics_table", {"metrics": METRICS})
        html = render.render_html(spec)
        assert "<table>" in html and "final_nav" in html


class TestReportBuilder:
    def test_mandatory_assumptions_section(self):
        html = ReportBuilder(_bundle(), _assumptions()).build()
        assert 'id="assumptions"' in html
        assert "假设与披露" in html

    def test_render_level_structure(self):
        """**渲染级断言**（Z-1/Z-2，19 号 §26.2 / 铁律新 14）。

        为什么必须有本条：正文 HTML 片段经 `autoescape=True` 被整体转义时，
        **标签失形而文本完好**——旧断言（`id="summary"`、文本子串、字节相等）
        对这类缺陷**结构性失明**，故「六图全灭 + 六章节卡表失形」逃过了 765
        个测试、五轮审查（第六种逃逸模式）。本断言落到**渲染后的结构**。
        """
        html = ReportBuilder(_bundle(), _assumptions()).build()
        assert "<div class='cards'>" in html, "指标卡未成形（正文被转义？）"
        assert "<div class='card'>" in html
        assert "<table><thead>" in html, "表格未成形（正文被转义？）"
        assert html.count("<script") >= 1, (
            "图表脚本被转义 → 六图不渲染（Z-1 回归）")
        assert "&lt;div class=" not in html, "正文仍被整体转义（Z-1 回归）"
        assert html.count("<section ") >= 6

    def test_assumptions_cannot_be_disabled(self):
        with pytest.raises(ReportError, match="强制章节"):
            ReportBuilder(_bundle(), _assumptions()).build(
                include_assumptions=False)

    def test_all_sections_present(self):
        html = ReportBuilder(_bundle(), _assumptions()).build()
        for section in ("summary", "performance", "trading", "assumptions",
                        "reproduce", "appendix"):
            assert f'id="{section}"' in html, section

    def test_assumption_contents_disclosed(self):
        html = ReportBuilder(_bundle(), _assumptions()).build()
        assert "next_open" in html                 # 撮合假设
        assert "0.001" in html and "0.0005" in html  # 印花税两段
        assert "v7.7" in html                      # 规则版本
        assert "已采样" in html                     # 降级事件

    def test_missing_fee_segments_flagged(self):
        """未注入费率分段 → 章节标注"未披露"（不静默省略）。"""
        html = ReportBuilder(_bundle(), Assumptions()).build()
        assert "未披露" in html

    def test_idempotent_regeneration(self):
        """13 §19.5：报告可再生成幂等（同输入 → 逐字节一致）。"""
        builder = ReportBuilder(_bundle(), _assumptions())
        assert builder.build() == builder.build()

    def test_chart_missing_degrades_report(self):
        """trades 为空 → 交易表缺失标注，报告其余章节照常（13 §19.6）。"""
        html = ReportBuilder(_bundle(with_trades=False), _assumptions()).build()
        assert "缺失" in html
        assert 'id="performance"' in html

    def test_rejections_distribution(self):
        html = ReportBuilder(_bundle(), _assumptions()).build()
        assert "insufficient_cash" in html

    def test_config_masked_in_appendix(self):
        """附录配置已脱敏（token → ***）。"""
        html = ReportBuilder(_bundle(), _assumptions()).build()
        assert "SECRET" not in html and "***" in html

    def test_cover_shows_four_versions(self):
        html = ReportBuilder(_bundle(), _assumptions()).build()
        assert "test_run_000000" in html
        assert "python" in html                  # env_version
