# -*- coding: utf-8 -*-
"""RC-07 邻域扫描对照夹具：**参数敏感（尖峰最优）**策略。

`x` 落在 `[0.95, 1.05]` 才建仓（否则持币）——用于构造"尖峰最优"网格：

- top1（`x=1.0`）建仓 → 收益为正；
- 邻域点（δ=0.1/0.2 → 0.8/0.9/1.1/1.2）全部持币 → 收益归零。

⇒ 相对退化率 ≈ 100% > 50%，`--perturb` 必须报**敏感**（与平坦对照
`ConfigBuyHold` 的双向探针见 `tests/unit/test_grid_search.py`）。
"""
from __future__ import annotations

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio


class ConfigSpiky(StrategyBase):
    """x ∈ [0.95, 1.05] 才按权重建仓（尖峰依赖）。"""

    #: S1 契约声明（runtime 对策略做版本协商）
    contract_version = CONTRACT_VERSION

    def __init__(self, symbol: str = "000001.SZ", x: float = 1.0,
                 weight: float = 0.9):
        self.symbol = symbol
        self.x = x
        self.weight = weight
        self._done = False

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        if self._done or not (0.95 <= self.x <= 1.05):
            return
        if ctx.bar(self.symbol) is not None:
            ctx.submit_target(TargetPortfolio(date, {self.symbol: self.weight}))
            self._done = True


__all__ = ["ConfigSpiky"]
