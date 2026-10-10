# -*- coding: utf-8 -*-
"""R58′（N60）测试夹具：**声明 lookback 的策略** 与 **短 lookback 对照策略**。

用途：Walk-Forward warm-up 语义的正面捕获网（19 号 §107.4 **A5**）——
长 lookback 策略在冷启动下 OOS **必然饿死**（0 成交）；给 warm-up 后恢复。

约定（R58′/§107.3.2-4）：策略以**类属性 `lookback_bars`** 声明所需历史 bar 数
（`declared_lookback()` 只读类属性，不装配、不运行）。
"""
from __future__ import annotations

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio


class LongLookbackStrategy(StrategyBase):
    """长 lookback（**声明 `lookback_bars = 40`**）：历史不足 ⇒ 无信号。

    语义：`ctx.history(n_bars=40)` 满 40 根后**建仓一次**并持有——
    冷启动（无 warm-up）时 30 日测试窗内永远凑不满 ⇒ OOS **0 成交**（N60 现象）；
    有 warm-up（≥40 交易日）⇒ 测试段首日即可建仓 ⇒ OOS **>0 成交**。
    """

    contract_version = CONTRACT_VERSION
    #: ← R58′ auto 推导读取（类属性；int）
    lookback_bars = 40

    def __init__(self, symbol: str = "000001.SZ", weight: float = 0.9):
        self.symbol = symbol
        self.weight = float(weight)
        self._done = False

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        if self._done:
            return
        bars = ctx.history(self.symbol, date, n_bars=self.lookback_bars)
        if len(bars) < self.lookback_bars:
            return                                   # 历史不足 ⇒ 饿死（不静默交易）
        ctx.submit_target(TargetPortfolio(date, {self.symbol: self.weight}))
        self._done = True


class ShortLookbackStrategy(StrategyBase):
    """短 lookback（**声明 `lookback_bars = 3`**）：3 根 bar 即建仓并持有。

    用途：**满足最小需求后 warm-up 不改变 OOS 结果**（不变性对照）。
    """

    contract_version = CONTRACT_VERSION
    lookback_bars = 3

    def __init__(self, symbol: str = "000001.SZ", weight: float = 0.9):
        self.symbol = symbol
        self.weight = float(weight)
        self._done = False

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        if self._done:
            return
        bars = ctx.history(self.symbol, date, n_bars=self.lookback_bars)
        if len(bars) < self.lookback_bars:
            return
        ctx.submit_target(TargetPortfolio(date, {self.symbol: self.weight}))
        self._done = True


__all__ = ["LongLookbackStrategy", "ShortLookbackStrategy"]
