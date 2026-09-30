# -*- coding: utf-8 -*-
"""Rebalancer：目标组合 → 订单清单（04 §8.3.5；M2 任务 5.3 FullRebalancer）。

差额法 + 整手化 + 卖先买后（M2 5.3 验收）；G7 资金链断言（S2 同日卖买
现金流守恒）经 tests/integration/test_rebalance_g7.py 验收；跟踪误差最
小化变体 **ThresholdRebalancer 已于 v0.5 V5-5 交付**。

确定性纪律：同 (target, portfolio, states, data) ⇒ 同订单清单——
订单排序固定（卖先买后，同侧按 symbol 字典序），数量只依赖输入。
"""
from __future__ import annotations

from collections.abc import Mapping

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.market import Bar, TradingState
from btf.domain.orders import Order, OrderSide, OrderType
from btf.domain.types import Instrument
from btf.portfolio.portfolio import Portfolio
from btf.strategy.rebalance import TargetPortfolio


class FullRebalancer:
    """全额再平衡（PoC 简版）。

    价基准：T 日收盘价（target 由 on_close 产出，⑥ 时点 data=T 截面）。
    """

    contract_version = CONTRACT_VERSION

    def __init__(self, instruments: Mapping[str, Instrument] | None = None):
        self._instruments = dict(instruments or {})

    def generate_orders(
        self,
        target: TargetPortfolio,
        portfolio: Portfolio,
        states: Mapping[str, TradingState],
        data: Mapping[str, Bar],
    ) -> list[Order]:
        """目标 → 草稿订单（order_id=""，引擎⑨入队时统一赋号）。

        结构（v0.5 V5-5 起拆两步，子类 ThresholdRebalancer 只覆写取量）：
            `_target_qty`：目标权重 → 目标股数（整手向下取整）
            差额组装：持仓未在目标 → 全清（含零股一次性了结）；在目标 → 差额
        """
        target_qty = self._target_qty(target, portfolio, data)
        sells: list[Order] = []
        buys: list[Order] = []
        universe = sorted(set(target_qty) | set(portfolio.positions))
        for sym in universe:
            cur = portfolio.positions[sym].qty if sym in portfolio.positions else 0
            want = target_qty.get(sym, 0)
            delta = want - cur
            if delta == 0:
                continue
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            qty = abs(delta)
            if side is OrderSide.BUY and qty < self._lot(sym):
                continue                     # 不足一手：不生成买单
            draft = Order(order_id="", symbol=sym, side=side,
                          order_type=OrderType.MARKET, qty=qty,
                          limit_price=None, created_at=target.rebalance_date,
                          tag="rebalance")
            (sells if side is OrderSide.SELL else buys).append(draft)
        # 卖先买后（T+1 现金链：回笼资金供买单；撮合同序）；同侧 symbol 序
        return sells + buys

    def _target_qty(
        self,
        target: TargetPortfolio,
        portfolio: Portfolio,
        data: Mapping[str, Bar],
    ) -> dict[str, int]:
        """目标权重 → 目标股数：权重×NAV / 收盘价，向下整手化。

        当日无行情的标的跳过（不生成订单 → 下轮再平衡补，06 §10.4）。
        """
        tv = portfolio.total_value()
        target_qty: dict[str, int] = {}
        for sym, weight in sorted(target.targets.items()):
            if weight <= 0:
                continue
            bar = data.get(sym)
            if bar is None or bar.close <= 0:
                continue
            lot = self._lot(sym)
            target_qty[sym] = int(tv * weight / bar.close // lot) * lot
        return target_qty

    def _lot(self, symbol: str) -> int:
        inst = self._instruments.get(symbol)
        return inst.lot_size if inst is not None else 100


class ThresholdRebalancer(FullRebalancer):
    """阈值再平衡（跟踪误差最小化变体，v0.5 V5-5）。

    与差额法的唯一差异（`_target_qty` 覆写）：**带内不动**——标的当前权重
    与目标权重偏离 ≤ threshold 时跳过该标的（want=cur → 无订单），避免
    微小偏离的换手磨损；偏离 > threshold 才交易到目标数量。目标外持仓
    （目标权重 0）**始终全清**（尘仓不滞留——跟踪误差最小化的本义）。

    - threshold=0 → 与 FullRebalancer **逐单一致**（对账断言锚点）；
    - 带判定用 T 日收盘价 × 持仓数量 / NAV（与目标数量同一价格基准）；
    - 卖先买后 / 整手化 / 无行情跳过等语义全部继承差额法。
    """

    contract_version = CONTRACT_VERSION

    def __init__(self, instruments: Mapping[str, Instrument] | None = None,
                 threshold: float = 0.05):
        super().__init__(instruments)
        if not 0 <= threshold < 1:
            raise ValueError(f"threshold 须 ∈ [0,1)，得 {threshold!r}")
        self.threshold = float(threshold)

    def _target_qty(
        self,
        target: TargetPortfolio,
        portfolio: Portfolio,
        data: Mapping[str, Bar],
    ) -> dict[str, int]:
        target_qty = super()._target_qty(target, portfolio, data)
        if self.threshold <= 0:
            return target_qty                  # 0 → 差额法全等
        tv = portfolio.total_value()
        if tv <= 0:
            return target_qty
        for sym, weight in target.targets.items():
            if sym not in target_qty:
                continue
            pos = portfolio.positions.get(sym)
            bar = data.get(sym)
            if pos is None or bar is None or bar.close <= 0:
                continue
            current_weight = pos.qty * bar.close / tv
            if abs(current_weight - weight) <= self.threshold:
                target_qty[sym] = pos.qty      # 带内：保持现状（不动）
        return target_qty


__all__ = ["FullRebalancer", "ThresholdRebalancer"]
