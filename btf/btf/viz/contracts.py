# -*- coding: utf-8 -*-
"""可视化数据契约：ChartQuery / ChartSpec（13 §19.2；M2 任务 5.8）。

解耦纪律（13 §19.1）：图表插件只消费 **ChartQuery**（声明式数据请求），
输出 **ChartSpec**（渲染无关的声明）；编排层（ReportBuilder）负责从
RunStore 供数 + 章节顺序；渲染层（viz/render.py）把 Spec 变成 HTML。
三层可独立替换（Plotly→ECharts 只动渲染层）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

#: 图表数据契约版本（与 RunStore 产物同版本演进，变更走 ADR——13 §19.2）
CHART_CONTRACT_VERSION = "runresult.v1"


@dataclass(frozen=True)
class ChartQuery:
    """图表插件的数据请求（声明式）：插件声明要什么，编排层从 RunStore 供给。"""

    run_id: str
    dataset: str                      # snapshots|trades|fills|rejections|metrics
    fields: Sequence[str] = ()
    filters: Mapping[str, Any] | None = None
    contract_version: str = CHART_CONTRACT_VERSION


@dataclass(frozen=True)
class SeriesSpec:
    """数据列绑定（图例 + 轴绑定 + 可选样式）。"""

    name: str
    values: Sequence[Any]
    axis: str = "y"                   # y | y2
    kind: str = "line"                # line|area|bar|heatmap|table
    style: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AxisSpec:
    """轴定义（标题/格式/对数）。"""

    title: str = ""
    format: str = ""                  # "" | "pct" | "date" | "log"
    range: Sequence[float] | None = None


@dataclass(frozen=True)
class AnnotationSpec:
    """标注（交易标记/事件标记）。"""

    x: Any
    y: Any
    text: str = ""
    kind: str = "point"               # point|vline|band


@dataclass(frozen=True)
class ChartSpec:
    """图表插件输出（渲染无关声明；可序列化——报告缓存/测试快照）。"""

    chart_type: str                   # line|heatmap|candle|area|table|scatter
    title: str
    subtitle: str | None = None
    series: Sequence[SeriesSpec] = ()
    axes: Mapping[str, AxisSpec] = field(default_factory=dict)
    annotations: Sequence[AnnotationSpec] = ()
    export_hint: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_type": self.chart_type, "title": self.title,
            "subtitle": self.subtitle,
            "series": [{"name": s.name, "values": list(s.values), "axis": s.axis,
                        "kind": s.kind, "style": dict(s.style)} for s in self.series],
            "axes": {k: {"title": v.title, "format": v.format,
                         "range": list(v.range) if v.range else None}
                     for k, v in self.axes.items()},
            "annotations": [{"x": a.x, "y": a.y, "text": a.text, "kind": a.kind}
                            for a in self.annotations],
            "export_hint": dict(self.export_hint or {}),
        }


class ChartPlugin(Protocol):
    """图表插件协议（13 §19.6：自定义图表零核心改动）。"""

    name: str

    def query(self, run_id: str) -> ChartQuery: ...

    def build(self, data: Mapping[str, Any]) -> ChartSpec: ...


__all__ = [
    "CHART_CONTRACT_VERSION",
    "AnnotationSpec",
    "AxisSpec",
    "ChartPlugin",
    "ChartQuery",
    "ChartSpec",
    "SeriesSpec",
]
