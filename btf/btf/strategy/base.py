# -*- coding: utf-8 -*-
"""策略基类（04 §8.3.2 协议；PoC-2 以 ABC 交付，钩子默认空实现）。

策略纪律（06 §8.1）：
    - 只经 ctx 访问数据（前视封堵在 ctx 断言）；
    - 只产出 TargetPortfolio（submit_target）或显式订单（submit_order）；
    - init（循环前一次）→ on_open（T 开盘后）→ on_close（T 收盘后）。
"""
from __future__ import annotations

from abc import ABC

from btf.domain.types import TradingDate
from btf.strategy.context import StrategyContext


class StrategyBase(ABC):  # noqa: B024 —— 有意无抽象方法：全部可选钩子
    """策略钩子集合（全部可选覆写）。"""

    def init(self, ctx: StrategyContext) -> None:  # noqa: B027 —— 默认空实现
        """回测开始前一次（预计算/缓存装载）。"""

    def on_open(self, ctx: StrategyContext, date: TradingDate) -> None:  # noqa: B027
        """T 日开盘钩子（撮合已完成，可读当日持仓现金）。"""

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:  # noqa: B027
        """T 日收盘钩子（默认决策点：产出目标组合）。"""


__all__ = ["StrategyBase"]
