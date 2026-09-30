# -*- coding: utf-8 -*-
"""月度等权策略（v0.5）：B3 全市场扫描 / B4 参数扫描 / V5-4 指数宇宙场景。

宇宙来源（构造参数二选一，`module:Class` 配置声明制加载）：
    - `symbols`：固定标的清单（`max_names` 截取前 N——B4 网格参数位）；
    - `index_universe`：月度成分映射 `{YYYYMM: [code]}`（runtime 在
      `universe.source=index` 时自动注入，V5-4；映射本身已按 PIT 口径
      解析（月宇宙 = 月首前最后快照），策略直接消费当月键）。

决策时点：每月**首个交易日**收盘提交目标（`rebalance_day` 放宽为
「自然日 ≤ N 的首个交易日」，N=1 即严格月首）；次日开盘执行（引擎
NextOpen 语义）。
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
