# -*- coding: utf-8 -*-
"""ChartSpec → HTML（13 §19.1 渲染层；M2 任务 5.8）。

后端：Plotly（`plotly` 属 viz 可选依赖，占用预算第 7 槽位）。纪律：
    - 渲染层**只认 ChartSpec**（不消费引擎/分析内部类型）——13 §19.1；
    - **降级**（13 §19.6）：plotly 未安装或渲染异常 → 回退**内置零依赖
      HTML/SVG 渲染器**（表格/折线的最小可用表达），并标注降级；不中断报告；
    - 自包含：plotly.js 仅首图内联（`include_plotlyjs=True`），其余复用
      （13 §19.4 "单文件自包含，可离线分享"）。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from btf.viz.contracts import ChartSpec

logger = logging.getLogger(__name__)

try:                                    # 可选依赖（viz extra）
    import plotly.graph_objects as go

    _PLOTLY = True
except ImportError:                     # pragma: no cover - 环境相关
    _PLOTLY = False
    go = None  # type: ignore[assignment]


def plotly_available() -> bool:
    """Plotly 后端可用性（报告"假设与披露"章节披露渲染后端）。"""
    return _PLOTLY


# ─────────────────────────────────────────────────────────────
# Plotly 后端
# ─────────────────────────────────────────────────────────────
def _figure(spec: ChartSpec) -> Any:
    """ChartSpec → plotly Figure（按 chart_type 分派）。"""
    x = list((spec.export_hint or {}).get("x") or [])
    if spec.chart_type == "heatmap":
        categories = (spec.export_hint or {}).get("categories") or {}
        z = list(spec.series[0].values) if spec.series else []
        return go.Figure(go.Heatmap(
            z=z, x=categories.get("x") or list(range(1, 13)),
            y=categories.get("y") or list(range(len(z))),
            colorscale="RdYlGn", hoverongaps=False))
    if spec.chart_type == "table":
        rows = list(spec.series[0].values) if spec.series else []
        header = list((spec.export_hint or {}).get("header") or [])
        return go.Figure(go.Table(
            header=dict(values=header, align="left"),
            cells=dict(values=[[r[i] for r in rows] for i in range(len(header))],
                       align="left")))
    fig = go.Figure()
    stack = any((s.style or {}).get("stack") for s in spec.series)
    for series in spec.series:
        xs = x or list(range(len(series.values)))
        common = {"name": series.name, "x": xs, "y": list(series.values)}
        if spec.chart_type == "area":
            fig.add_trace(go.Scatter(
                **common, mode="lines",
                stackgroup="one" if stack else None,
                fill="tonexty" if not stack else None))
        else:
            fig.add_trace(go.Scatter(**common, mode="lines"))
    y_fmt = (spec.axes.get("y").format if spec.axes.get("y") else "")
    fig.update_layout(
        title=spec.title,
        xaxis_title=(spec.axes.get("x").title if spec.axes.get("x") else ""),
        yaxis_title=(spec.axes.get("y").title if spec.axes.get("y") else ""),
        yaxis_ticksuffix="%" if y_fmt == "pct" else "",
        hovermode="x unified",
    )
    for ann in spec.annotations:
        fig.add_annotation(x=ann.x, y=ann.y, text=ann.text, showarrow=True)
    return fig


def _div_id(spec: ChartSpec) -> str:
    """确定性 div id（13 §19.5 幂等：Plotly 默认随机 UUID 会破坏可再生成）。"""
    digest = hashlib.sha1(
        f"{spec.chart_type}|{spec.title}".encode()).hexdigest()[:12]
    return f"chart-{digest}"


def render_html(spec: ChartSpec, *, include_plotlyjs: bool = False) -> str:
    """ChartSpec → HTML 片段（Plotly；不可用时降级内置渲染器）。"""
    if not _PLOTLY:
        return _fallback_html(spec)
    try:
        return _figure(spec).to_html(
            full_html=False, include_plotlyjs=include_plotlyjs,
            div_id=_div_id(spec),
            config={"displaylogo": False, "responsive": True},
        )
    except Exception as exc:                      # 降级：不中断报告
        logger.warning("图表 %s 渲染降级为内置 HTML（%s）", spec.title, exc)
        return _fallback_html(spec)


# ─────────────────────────────────────────────────────────────
# 内置零依赖回退（保证"无 plotly 也能出报告"）
# ─────────────────────────────────────────────────────────────
def _esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _fallback_html(spec: ChartSpec) -> str:
    """内置渲染：表格化/最小值序列（自包含，无 JS）。"""
    if spec.chart_type == "table":
        header = list((spec.export_hint or {}).get("header") or [])
        rows = list(spec.series[0].values) if spec.series else []
        head = "".join(f"<th>{_esc(h)}</th>" for h in header)
        body = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row)
                       + "</tr>" for row in rows)
        return (f"<section class='chart'><h3>{_esc(spec.title)}</h3>"
                f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody>"
                f"</table></section>")
    values = list(spec.series[0].values) if spec.series else []
    if not values:
        return f"<section class='chart'><h3>{_esc(spec.title)}</h3></section>"
    if spec.chart_type == "heatmap":
        body = "".join(
            "<tr>" + "".join("<td>" + ("" if v is None else f"{v:.2f}")
                             + "</td>" for v in row) + "</tr>"
            for row in values)
        return (f"<section class='chart'><h3>{_esc(spec.title)}（月收益 %）</h3>"
                f"<table><tbody>{body}</tbody></table></section>")
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    points = " ".join(
        f"{i / max(1, len(values) - 1) * 100:.3f},"
        f"{100 - (v - lo) / span * 100:.3f}" for i, v in enumerate(values))
    return (f"<section class='chart'><h3>{_esc(spec.title)}</h3>"
            f"<svg viewBox='0 0 100 100' preserveAspectRatio='none' "
            f"class='sparkline'><polyline points='{points}' fill='none' "
            f"stroke='#2b6cb0' stroke-width='0.6'/></svg>"
            f"<p class='note'>最小值 {lo:.4f} / 最大值 {hi:.4f}"
            f"（内置降级渲染）</p></section>")


__all__ = ["plotly_available", "render_html"]
