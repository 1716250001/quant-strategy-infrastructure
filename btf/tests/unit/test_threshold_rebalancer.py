# -*- coding: utf-8 -*-
"""L1 单测：ThresholdRebalancer 阈值再平衡（v0.5 V5-5；18 号计划）。

验收锚点（V5-5）：与差额法对账（threshold=0 逐单一致）+ 阈值触发断言
（带内不动 / 带外交易到目标 / 目标外持仓始终全清）。
"""
from __future__ import annotations

import pytest
from btf.domain.market import Bar
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.portfolio.portfolio import Portfolio
from btf.portfolio.rebalancer import FullRebalancer, ThresholdRebalancer
from btf.strategy.rebalance import TargetPortfolio

from tests.fixtures.scenarios import make_state

pytestmark = [pytest.mark.l1]

D1 = TradingDate.from_ymd("20200106")
A, B, C = "000001.SZ", "600000.SH", "300750.SZ"

INST = {s: Instrument(symbol=s, asset_class=AssetClass.STOCK, board="main",
                      lot_size=100) for s in (A, B, C)}


def _data(closes: dict[str, float]) -> dict[str, Bar]:
    return {sym: Bar(symbol=sym, date=D1, open=px, high=px, low=px, close=px,
                     volume=1e6, amount=px * 1e6, pre_close=px)
            for sym, px in closes.items()}


def _portfolio(cash: float, holdings: dict[str, int],
               closes: dict[str, float]) -> Portfolio:
    """建仓组合（与引擎同一路径：handler._fill → apply_fill）。"""
    from btf.domain.orders import Order, OrderSide, OrderType
    from btf.execution.cost import ZeroCostModel
    from btf.execution.handler import NextOpenHandler

    handler = NextOpenHandler(cost_model=ZeroCostModel(), instruments=INST)
    feed_bars = _data(closes)
    pf = Portfolio(cash=cash)
    for sym, qty in holdings.items():
        order = Order(order_id=f"O-{sym}", symbol=sym, side=OrderSide.BUY,
                      order_type=OrderType.MARKET, qty=qty, limit_price=None,
                      created_at=D1)
        fill = handler._fill(order, D1, feed_bars,
                             {sym: make_state(sym, D1, pre=closes[sym])})
        pf.apply_fill(fill)
    pf.advance_day()
    return pf


def _states(symbols, closes):
    return {sym: make_state(sym, D1, pre=closes[sym]) for sym in symbols}


class TestEquivalenceWithFull:
    """threshold=0 → 与差额法**逐单一致**（V5-5 对账验收）。"""

    def test_zero_threshold_identical_orders(self):
        closes = {A: 10.0, B: 20.0}
        holdings = {A: 5_000, B: 2_000}
        target = TargetPortfolio(D1, {A: 0.5, B: 0.4})
        for pf_seed in (500_000.0, 1_000_000.0):
            pf1 = _portfolio(pf_seed, holdings, closes)
            pf2 = _portfolio(pf_seed, holdings, closes)
            data = _data(closes)
            states = _states([A, B], closes)
            full = FullRebalancer(instruments=INST).generate_orders(
                target, pf1, states, data)
            zero = ThresholdRebalancer(instruments=INST,
                                       threshold=0.0).generate_orders(
                target, pf2, states, data)
            assert [(o.symbol, o.side, o.qty) for o in full] == \
                   [(o.symbol, o.side, o.qty) for o in zero]

    def test_invalid_threshold_rejected(self):
        with pytest.raises(ValueError, match="threshold"):
            ThresholdRebalancer(threshold=1.0)


class TestBandSemantics:
    """阈值触发：带内不动 / 带外交易到目标。"""

    def test_within_band_skips(self):
        """当前权重 0.42 vs 目标 0.45，偏离 0.03 ≤ 0.05 → 无订单。"""
        closes = {A: 10.0}
        pf = _portfolio(580_000.0, {A: 5_000}, closes)   # mv=50,000 tv=630,000
        cur_w = 5_000 * 10.0 / 630_000
        target_w = cur_w + 0.03
        target = TargetPortfolio(D1, {A: target_w})
        orders = ThresholdRebalancer(instruments=INST,
                                     threshold=0.05).generate_orders(
            target, pf, _states([A], closes), _data(closes))
        assert orders == []

    def test_beyond_band_trades_to_target(self):
        """当前权重 0.42 vs 目标 0.55，偏离 0.13 > 0.05 → 交易到目标数量。"""
        closes = {A: 10.0}
        pf = _portfolio(580_000.0, {A: 5_000}, closes)
        target_w = 5_000 * 10.0 / 630_000 + 0.13
        target = TargetPortfolio(D1, {A: target_w})
        data = _data(closes)
        orders = ThresholdRebalancer(instruments=INST,
                                     threshold=0.05).generate_orders(
            target, pf, _states([A], closes), data)
        full_orders = FullRebalancer(instruments=INST).generate_orders(
            TargetPortfolio(D1, {A: target_w}), pf, _states([A], closes), data)
        assert len(orders) == 1 and orders[0].side.value == "buy"
        assert orders[0].qty == full_orders[0].qty, "带外数量 = 差额法目标"

    def test_target_absent_position_always_liquidated(self):
        """目标外持仓（目标权重 0）始终全清——即使权重 < threshold。"""
        closes = {A: 10.0, B: 20.0}
        pf = _portfolio(998_000.0, {A: 200, B: 1_000}, closes)  # B 权重 0.02
        target = TargetPortfolio(D1, {A: 0.9})                  # B 不在目标
        orders = ThresholdRebalancer(instruments=INST,
                                     threshold=0.05).generate_orders(
            target, pf, _states([A, B], closes), _data(closes))
        sells = [o for o in orders if o.side.value == "sell"]
        assert sells and sells[0].symbol == B and sells[0].qty == 1_000

    def test_unheld_symbol_buys_regardless_of_band(self):
        """未持有标的（当前权重 0）：目标权重 ≤ threshold → 差额法跳过
        （不足一手不买单语义继承）；> threshold → 正常建仓。"""
        closes = {A: 10.0}
        pf = _portfolio(1_000_000.0, {}, closes)
        target = TargetPortfolio(D1, {A: 0.3})
        orders = ThresholdRebalancer(instruments=INST,
                                     threshold=0.05).generate_orders(
            target, pf, _states([A], closes), _data(closes))
        assert len(orders) == 1 and orders[0].side.value == "buy"


class TestRegistryWiring:
    """registry 名字表 + runtime 配置声明制。"""

    def test_registered_names(self):
        from btf import registry

        assert "threshold" in registry.available(registry.REBALANCER)
        assert "full" in registry.available(registry.REBALANCER)

    def test_create_with_threshold_param(self):
        from btf import registry

        r = registry.create(registry.REBALANCER, "threshold",
                            {"instruments": INST, "threshold": 0.1})
        assert isinstance(r, ThresholdRebalancer) and r.threshold == 0.1
