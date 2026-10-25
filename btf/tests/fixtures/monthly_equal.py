# -*- coding: utf-8 -*-
"""测试夹具：月度等权策略（B3/B4/V5-4 工具不变量用）。

LL-4（19 号 §133.2）：仓内 `monthly` 策略实现已迁仓外；本夹具是其在 **btf
测试域**内的等价替身（`tests/fixtures/` 属审查方明确豁免的测试夹具）。
行为逐行对齐原实现——**只用于工具不变量，不作为策略实现对外提供**。
"""
from __future__ import annotations

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio


class MonthlyEqualWeight(StrategyBase):
    """每月首个交易日等权持有宇宙（B3 扫描语义 / B4 网格参数位）。"""

    #: S1 契约声明（v0.5.1 起 runtime 对策略做版本协商——19 号 P1-2/EX-2）
    contract_version = CONTRACT_VERSION

    def __init__(
        self,
        symbols: list[str] | None = None,
        index_universe: dict[str, list[str]] | None = None,
        max_names: int | None = None,
        rebalance_day: int = 1,
    ):
        if symbols is None and index_universe is None:
            raise ValueError(
                "MonthlyEqualWeight 需要 symbols 或 index_universe 之一"
                "（后者由 runtime universe.source=index 自动注入）")
        if symbols is not None:
            symbols = list(symbols)
            if max_names is not None:
                symbols = symbols[:max(1, int(max_names))]
        if not 1 <= rebalance_day <= 28:
            raise ValueError(f"rebalance_day 须 ∈ [1,28]，得 {rebalance_day!r}")
        self._symbols = symbols
        self._index_universe = index_universe
        self._rebalance_day = int(rebalance_day)
        self._last_month: str | None = None

    def _universe_of(self, month: str) -> list[str]:
        if self._symbols is not None:
            return self._symbols
        return self._index_universe.get(month, [])   # PIT 已在 provider 解析

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        month = date.to_ymd()[:6]
        if month == self._last_month:
            return
        if date.iso.day > self._rebalance_day:       # 月内已过决策窗口
            return
        picks = self._universe_of(month)
        if not picks:
            return                                   # 诚实无数据（不前向填充）
        self._last_month = month
        weight = 1.0 / len(picks)
        ctx.submit_target(
            TargetPortfolio(date, {code: weight for code in picks}))


__all__ = ["MonthlyEqualWeight"]
