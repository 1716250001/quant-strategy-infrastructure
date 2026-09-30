# -*- coding: utf-8 -*-
"""Analyzer / Optimizer 协议（04 §8.3.6；M1 任务 4.1 契约定稿）。

实现进度：15 项核心指标 → 任务 5.4 **已交付**（NAV 四式 04 §8.2.6 N5-1）；
GridSearch → 优化器（进程池）**归 v0.5**（M1 只冻结签名）。

分层注记：analytics 层位于 portfolio 之下（03 §7.2）——快照/成交类型
以 Any 结构化声明（PortfolioSnapshot/Trade 由装配期注入，协议不
import 外层模块）。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

#: 指标值：标量（NaN 允许 = 该指标降级不中断，04 §8.5 扩展点 8）或分组映射
MetricValue = float | Mapping[str, float]
#: 参数空间：参数名 → 候选序列（GridSearch 口径；M2 细化）
ParamSpace = Mapping[str, Sequence[Any]]
#: 优化预算：max_runs / timeout_s 等（M2 细化）
OptBudget = Mapping[str, Any]
#: 单次回测调用：参数 → RunResult（engine 组合，Optimizer 不含回测逻辑）
Runner = Callable[[Mapping[str, Any]], Any]


class Analyzer(Protocol):
    """绩效指标插件（纯函数：PortfolioSnapshot 序列 → 指标值）。"""

    name: str

    def compute(self, snapshots: Sequence[Any], trades: Sequence[Any],
                config: Mapping[str, Any]) -> MetricValue: ...


class Optimizer(Protocol):
    """优化器插件：参数空间 × 引擎调用 → 结果矩阵（组合 api.run）。"""

    def search(self, param_space: ParamSpace, runner: Runner,
               budget: OptBudget) -> list[Mapping[str, Any]]: ...


__all__ = ["Analyzer", "MetricValue", "OptBudget", "Optimizer", "ParamSpace", "Runner"]
