# -*- coding: utf-8 -*-
"""目标组合声明（04 §8.2.7）：策略产出意图，Rebalancer 负责订单实现。

策略 ⇄ 执行解耦纪律：策略只声明目标权重（可单测、可离线重构），
永不直接构造订单；订单生成（差额/整手化/排序）集中于 Rebalancer，
保证「同一目标 ⇒ 同一订单清单」的可复现性。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from btf.domain.types import TradingDate


@dataclass(frozen=True)
class TargetPortfolio:
    """再平衡目标（T 日收盘决策 → T+1 开盘执行，06 §10.1 默认时序）。

    targets：symbol → 权重（闭区间 [0,1]，Σ ≤ 1；未列出的持仓标的=清仓；
    余量（1−Σ）为现金）。
    """

    rebalance_date: TradingDate
    targets: Mapping[str, float]


__all__ = ["TargetPortfolio"]
