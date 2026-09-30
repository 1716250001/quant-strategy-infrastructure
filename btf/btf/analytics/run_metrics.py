# -*- coding: utf-8 -*-
"""一次运行的指标计算（**三处调用链的单点**；R4 剩余，19 号 §41.5）。

此前同一件事在三处各写一遍（口径漂移风险）：

| 调用点 | 原写法 |
|---|---|
| `runtime.run()`（落盘） | `compute_all(snapshots, fills, _analysis_config(), analyzer_names=_analyzer_names())` + 基准项合并 |
| `runtime.verify_run()`（复核） | `compute_all(...)` + `resolver=registry.resolve` + 基准项合并（基准号取自 manifest） |
| `optimize/grid.py`（网格组合） | `compute_all(result.snapshots, result.fills, rt._analysis_config())`（**未**带 analyzer_names） |

本模块把它们收口为一个函数：**"一次运行该怎么算指标"只有一处定义**。

纪律：`extras` 只接受 `Callable[[Sequence], Mapping]`——基准相对指标由调用方
注入（runtime 提供；网格场景不传 ⇒ 与既有行为一致，不偷偷改变结果）。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from btf.analytics.metrics import compute_all

__all__ = ["compute_run_metrics"]


def compute_run_metrics(
    snapshots: Sequence[Any],
    fills: Sequence[Any],
    config: Mapping[str, Any] | None = None,
    *,
    analyzer_names: Sequence[str] | None = None,
    resolver: Callable[[str], Any] | None = None,
    extras: Callable[[Sequence[Any]], Mapping[str, Any]] | None = None,
) -> dict[str, float]:
    """一次运行的指标 = 基础指标（+ 自定义名字表）+ 可选 extras（基准相对项）。

    参数：
        snapshots / fills：运行或产物回读的快照与成交；
        config：`analysis` 段（年化/无风险利率/指标参数）；
        analyzer_names：生效指标名字表（None = 全量 15 项）；
        resolver：自定义指标的解析器（registry 名字表）；
        extras：`(snapshots) -> {指标名: 值}`（如基准相对指标），**可空**。
    """
    metrics = compute_all(snapshots, fills, config or {},
                          analyzer_names=list(analyzer_names)
                          if analyzer_names is not None else None,
                          resolver=resolver)
    if extras is not None:
        metrics.update(extras(snapshots) or {})
    return metrics
