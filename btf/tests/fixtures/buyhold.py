# -*- coding: utf-8 -*-
"""动态加载冒烟策略（M1 任务 4.1 BTFRuntime 纵切测试）。

module:Class 加载范式（06 §11.5 run.strategy）：构造参数经 run.params
注入（装配期——与 doc §8.3.2 init(ctx, config) 的已实证偏差，见
btf/strategy/base.py 模块注记）。
"""
from __future__ import annotations

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio


class ConfigBuyHold(StrategyBase):
    """首日按目标权重买入并持有（symbol/weight 经构造参数注入）。

    weight 默认 1.0；上涨场景（次日开盘价 > 定量收盘价）需 <1 才可成
    （INSUFFICIENT_CASH 逐单扣减语义，handler.py）。
    """

    #: S1 契约声明（v0.5.1 起 runtime 对策略做版本协商——19 号 P1-2/EX-2）
    contract_version = CONTRACT_VERSION

    def __init__(self, symbol: str = "000001.SZ", weight: float = 1.0):
        self.symbol = symbol
        self.weight = weight
        self._done = False

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        if not self._done and ctx.bar(self.symbol) is not None:
            ctx.submit_target(TargetPortfolio(date, {self.symbol: self.weight}))
            self._done = True


__all__ = ["ConfigBuyHold"]
