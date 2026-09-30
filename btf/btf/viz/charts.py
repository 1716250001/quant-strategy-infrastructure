# -*- coding: utf-8 -*-
"""六张核心图表插件（13 §19.3 图 1-6；M2 任务 5.8）。

插件只消费 RunStore 产物（snapshots/trades/fills/rejections/metrics），
产出 ChartSpec（渲染无关）——不 import 引擎/分析内部（13 §19.1 解耦纪律）。

| 插件名 | 图表 | 数据源 |
|---|---|---|
| equity | 资金曲线 | snapshots.total_value |
| drawdown | 回撤水下图 | snapshots.drawdown |
| monthly_heatmap | 月度收益热力图 | snapshots.daily_return（按月累乘） |
| trades_table | 交易明细表 | trades |
| metrics_table | 核心指标表 | metrics |
| weights_area | 持仓权重面积图 | snapshots.weights（前 N + 现金） |

降级（13 §19.6）：数据缺失/插件异常 → 编排层跳过该图并标注（不中断报告）。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from btf.viz.contracts import (
    AnnotationSpec,
    AxisSpec,
    ChartPlugin,
    ChartQuery,
    ChartSpec,
    SeriesSpec,
)

#: 快照权重表的现金保留键（与 portfolio.CASH_KEY 同值；分层纪律：viz 不 import
#  portfolio——铁律 5 表现层只认数据契约，故此处本地声明）
CASH_KEY = "@CASH"

#: 权重面积图的持仓展示上限（其余并入"其他"）
TOP_HOLDINGS = 8


def _month_key(ymd: str) -> tuple[str, int]:
    return ymd[:4], int(ymd[4:6])


class EquityChart:
    """图 1 资金曲线（策略 NAV；基准线数据缺失时省略——13 §19.3）。"""

    name: ClassVar[str] = "equity"

    def query(self, run_id: str) -> ChartQuery:
        return ChartQuery(run_id=run_id, dataset="snapshots",
                          fields=["date", "total_value"])

    def build(self, data: Mapping[str, Any]) -> ChartSpec:
        snaps = list(data.get("snapshots") or [])
        if not snaps:
            raise ValueError("snapshots 为空——资金曲线无法渲染")
        dates = [s.date for s in snaps]
        nav = [s.total_value for s in snaps]
        return ChartSpec(
            chart_type="line", title="资金曲线", subtitle=f"{len(snaps)} 个交易日",
            series=[SeriesSpec(name="策略 NAV", values=nav, kind="line")],
            axes={"x": AxisSpec(title="日期", format="date"),
                  "y": AxisSpec(title="总资产（元）")},
            export_hint={"filename": "equity", "x": dates},
        )


class DrawdownChart:
    """图 2 回撤水下图（负轴填充；最大回撤点标注）。"""

    name: ClassVar[str] = "drawdown"

    def query(self, run_id: str) -> ChartQuery:
        return ChartQuery(run_id=run_id, dataset="snapshots",
                          fields=["date", "drawdown"])

    def build(self, data: Mapping[str, Any]) -> ChartSpec:
        snaps = list(data.get("snapshots") or [])
        if not snaps:
            raise ValueError("snapshots 为空——回撤图无法渲染")
        drawdowns = [s.drawdown * 100.0 for s in snaps]
        worst = min(range(len(drawdowns)), key=lambda i: drawdowns[i])
        return ChartSpec(
            chart_type="area", title="回撤水下图", subtitle="相对历史峰值",
            series=[SeriesSpec(name="回撤 %", values=drawdowns, kind="area")],
            axes={"x": AxisSpec(title="日期", format="date"),
                  "y": AxisSpec(title="回撤（%）")},
            annotations=[AnnotationSpec(x=snaps[worst].date, y=drawdowns[worst],
                                        text=f"最大回撤 {drawdowns[worst]:.2f}%")],
            export_hint={"filename": "drawdown", "x": [s.date for s in snaps]},
        )


class MonthlyHeatmapChart:
    """图 3 月度收益热力图（年×月；月内日收益累乘）。"""

    name: ClassVar[str] = "monthly_heatmap"

    def query(self, run_id: str) -> ChartQuery:
        return ChartQuery(run_id=run_id, dataset="snapshots",
                          fields=["date", "daily_return"])

    def build(self, data: Mapping[str, Any]) -> ChartSpec:
        snaps = list(data.get("snapshots") or [])
        if not snaps:
            raise ValueError("snapshots 为空——热力图无法渲染")
        monthly: dict[tuple[str, int], float] = {}
        for snap in snaps:
            key = _month_key(snap.date)
            monthly[key] = (1 + monthly.get(key, 0.0)) * (1 + snap.daily_return) - 1
        years = sorted({y for y, _ in monthly})
        months = list(range(1, 13))
        z = [[round(monthly.get((y, m), 0.0) * 100, 4) if (y, m) in monthly else None
              for m in months] for y in years]
        return ChartSpec(
            chart_type="heatmap", title="月度收益热力图", subtitle="单位 %",
            series=[SeriesSpec(name="月收益", values=z, kind="heatmap")],
            axes={"x": AxisSpec(title="月份"),
                  "y": AxisSpec(title="年份")},
            export_hint={"categories": {"x": [str(m) for m in months],
                                        "y": years}},
        )


class TradesTableChart:
    """图 4 交易明细表（trade_id/symbol/qty/pnl/holding_days）。"""

    name: ClassVar[str] = "trades_table"

    def query(self, run_id: str) -> ChartQuery:
        return ChartQuery(run_id=run_id, dataset="trades",
                          fields=["trade_id", "symbol", "qty", "pnl",
                                  "holding_days"])

    def build(self, data: Mapping[str, Any]) -> ChartSpec:
        trades = list(data.get("trades") or [])
        header = ["trade_id", "symbol", "qty", "pnl", "holding_days", "status"]
        rows: list[list[Any]] = []
        for t in trades:
            status = "平仓" if t.get("close_fill") else "持有"
            rows.append([t.get("trade_id"), t.get("symbol"), t.get("qty"),
                         round(float(t.get("pnl", 0.0)), 2),
                         t.get("holding_days"), status])
        if not rows:
            raise ValueError("trades 为空——交易表无内容（报告标注缺失）")
        return ChartSpec(
            chart_type="table", title="交易明细",
            subtitle=f"{len(rows)} 笔（含未平仓）",
            series=[SeriesSpec(name="rows", values=rows, kind="table")],
            axes={}, export_hint={"header": header},
        )


class MetricsTableChart:
    """图 5 核心指标表（15 项；NaN 显示"—"（降级））。"""

    name: ClassVar[str] = "metrics_table"

    def query(self, run_id: str) -> ChartQuery:
        return ChartQuery(run_id=run_id, dataset="metrics", fields=["*"])

    def build(self, data: Mapping[str, Any]) -> ChartSpec:
        metrics = dict(data.get("metrics") or {})
        if not metrics:
            raise ValueError("metrics 为空——指标表无法渲染")
        rows = [[name, "—" if value != value else round(float(value), 6)]
                for name, value in sorted(metrics.items())]
        return ChartSpec(
            chart_type="table", title="核心指标", subtitle=f"{len(rows)} 项",
            series=[SeriesSpec(name="rows", values=rows, kind="table")],
            axes={}, export_hint={"header": ["指标", "数值"]},
        )


class WeightsAreaChart:
    """图 6 持仓权重面积图（前 N 持仓 + 现金；其余并入"其他"）。"""

    name: ClassVar[str] = "weights_area"

    def query(self, run_id: str) -> ChartQuery:
        return ChartQuery(run_id=run_id, dataset="snapshots",
                          fields=["date", "weights"])

    def build(self, data: Mapping[str, Any]) -> ChartSpec:
        snaps = list(data.get("snapshots") or [])
        if not snaps:
            raise ValueError("snapshots 为空——权重图无法渲染")
        last = dict(snaps[-1].weights or {})
        ranked = sorted((k for k in last if k != CASH_KEY),
                        key=lambda k: -abs(last[k]))[:TOP_HOLDINGS]
        keys = ranked + ([CASH_KEY] if CASH_KEY in last else [])
        series = [
            SeriesSpec(name=key,
                       values=[float(dict(s.weights or {}).get(key, 0.0)) * 100.0
                               for s in snaps],
                       kind="area", style={"stack": True})
            for key in keys
        ]
        others = [
            round((1.0 - sum(float(dict(s.weights or {}).get(k, 0.0))
                             for k in keys)) * 100.0, 6)
            for s in snaps
        ]
        if any(abs(v) > 1e-9 for v in others):
            series.append(SeriesSpec(name="其他", values=others, kind="area",
                                     style={"stack": True}))
        return ChartSpec(
            chart_type="area", title="持仓权重", subtitle="堆叠面积（%）",
            series=series,
            axes={"x": AxisSpec(title="日期", format="date"),
                  "y": AxisSpec(title="权重（%）")},
            export_hint={"filename": "weights", "x": [s.date for s in snaps]},
        )


#: 图表插件名字表（配置声明制：报告按名字选图，13 §19.6）
CHART_PLUGINS: dict[str, type[ChartPlugin]] = {
    cls.name: cls  # type: ignore[attr-defined]
    for cls in (EquityChart, DrawdownChart, MonthlyHeatmapChart,
                TradesTableChart, MetricsTableChart, WeightsAreaChart)
}

#: 默认报告图序（13 §19.4 章节编排）
DEFAULT_CHARTS: tuple[str, ...] = ("equity", "drawdown", "monthly_heatmap",
                                   "metrics_table", "trades_table",
                                   "weights_area")


def build_chart(name: str, data: Mapping[str, Any]) -> ChartSpec:
    """按名字构建图表（未知名 → KeyError）。"""
    plugin_cls = CHART_PLUGINS[name]
    return plugin_cls().build(data)  # type: ignore[call-arg]


__all__ = [
    "CHART_PLUGINS",
    "DEFAULT_CHARTS",
    "DrawdownChart",
    "EquityChart",
    "MetricsTableChart",
    "MonthlyHeatmapChart",
    "TradesTableChart",
    "WeightsAreaChart",
    "build_chart",
]
