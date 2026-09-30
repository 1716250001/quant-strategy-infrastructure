# -*- coding: utf-8 -*-
"""L1 单测：VPP 成交量参与率上限（v0.5 V5-6；14 号 §24「成交量 × 5% 参与率」）。

语义锚点（`NextOpenHandler.volume_participation`）：
    - None（缺省）→ v0.2「全成或全拒」语义不变（回归保护）；
    - (0,1] → 成交量 = min(委托量, bar.volume × rate)：
        · 买入按整手向下取整；cap < 一手 → VOLUME_CAP 完全拒单；
        · 卖出按整数股（零股了结允许）；
        · 部分成交的余量**当日取消**（TIF=day，不排队）——vpp_stats 留痕；
        · 现金检查按受限后数量预估（大委托不因名义全额误拒）。
"""
from __future__ import annotations

import pytest
from btf.data.memory import MemoryFeed
from btf.domain.orders import Order, OrderSide, OrderType, RejectCode
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.execution.cost import FlatRateCostModel
from btf.execution.handler import NextOpenHandler
from btf.portfolio.portfolio import Portfolio

from tests.fixtures.scenarios import make_bar, make_state

pytestmark = [pytest.mark.l1]

D1 = TradingDate.from_ymd("20200106")
D2 = TradingDate.from_ymd("20200107")
SYM = "600000.SH"


def _order(side: OrderSide, qty: int) -> Order:
    return Order(order_id="O1", symbol=SYM, side=side,
                 order_type=OrderType.MARKET, qty=qty,
                 limit_price=None, created_at=D1)


def _feed(vol: float) -> tuple[MemoryFeed, dict[str, Instrument]]:
    bars = [make_bar(SYM, D1, 10.0, 10.5, 9.8, 10.2, 10.0, vol=vol),
            make_bar(SYM, D2, 10.1, 10.6, 9.9, 10.3, 10.2, vol=vol)]
    states = {(D1.to_ymd(), SYM): make_state(SYM, D1, pre=10.0),
              (D2.to_ymd(), SYM): make_state(SYM, D2, pre=10.2)}
    inst = {SYM: Instrument(symbol=SYM, asset_class=AssetClass.STOCK,
                            board="main", lot_size=100)}
    return MemoryFeed(bars, states, instruments=inst, dates=[D1, D2]), inst


def _section(feed: MemoryFeed, day: TradingDate) -> dict:
    for _d, section in feed.bars(None, day, day):
        return dict(section)
    raise AssertionError


class TestVppDisabled:
    """volume_participation=None → v0.2 语义（回归保护）。"""

    def test_no_vpp_fills_full_qty(self):
        feed, inst = _feed(vol=1_000.0)        # 量再小也全额成交（v0.2 语义）
        pf = Portfolio(cash=2_000_000.0)
        h = NextOpenHandler(cost_model=FlatRateCostModel(), instruments=inst)
        fills, rejs = h.execute([_order(OrderSide.BUY, 100_000)], D1,
                                _section(feed, D1),
                                feed.trading_states(D1), pf)
        assert not rejs and fills[0].qty == 100_000
        assert h.vpp_stats == {"partial_fills": 0, "unfilled_qty": 0}


class TestVppPartialFill:
    """cap 够一手 → 部分成交；余量当日取消并留痕。"""

    def test_buy_capped_to_lot_multiple(self):
        feed, inst = _feed(vol=30_000.0)       # 30,000 × 5% = 1,500 股
        pf = Portfolio(cash=1_000_000.0)
        h = NextOpenHandler(cost_model=FlatRateCostModel(), instruments=inst,
                            volume_participation=0.05)
        fills, rejs = h.execute([_order(OrderSide.BUY, 100_000)], D1,
                                _section(feed, D1),
                                feed.trading_states(D1), pf)
        assert not rejs
        assert fills[0].qty == 1_500            # min(100000, 30000×5% 整手化)
        assert h.vpp_stats == {"partial_fills": 1, "unfilled_qty": 98_500}
        assert pf.position(SYM).qty == 1_500    # 入账=受限数量

    def test_buy_below_cap_fills_fully(self):
        feed, inst = _feed(vol=1_000_000.0)     # cap=50,000 ≥ 委托 2,000
        pf = Portfolio(cash=1_000_000.0)
        h = NextOpenHandler(cost_model=FlatRateCostModel(), instruments=inst,
                            volume_participation=0.05)
        fills, rejs = h.execute([_order(OrderSide.BUY, 2_000)], D1,
                                _section(feed, D1),
                                feed.trading_states(D1), pf)
        assert not rejs and fills[0].qty == 2_000
        assert h.vpp_stats["partial_fills"] == 0

    def test_sell_capped_no_lot_floor(self):
        """卖出受限按整数股（不整手化——零股了结允许）。"""
        feed, inst = _feed(vol=30_000.0)
        pf = Portfolio(cash=1_000_000.0)
        h = NextOpenHandler(cost_model=FlatRateCostModel(), instruments=inst)
        fills, _ = h.execute([_order(OrderSide.BUY, 1_000)], D1,
                             _section(feed, D1), feed.trading_states(D1), pf)
        assert fills[0].qty == 1_000
        pf.advance_day()
        h2 = NextOpenHandler(cost_model=FlatRateCostModel(), instruments=inst,
                             volume_participation=0.05)
        fills, rejs = h2.execute([_order(OrderSide.SELL, 1_000)], D2,
                                 _section(feed, D2),
                                 feed.trading_states(D2), pf)
        assert not rejs and fills[0].qty == 1_000   # cap=1500 ≥ 1000 → 全成

    def test_cash_check_uses_capped_qty(self):
        """现金检查按受限后数量：委托名义额超现金但 cap 后可成交 → 不误拒。"""
        feed, inst = _feed(vol=30_000.0)          # cap=1,500 × 10.0 = 15,000 元
        pf = Portfolio(cash=20_000.0)             # < 100,000×10 名义但 > cap 额
        h = NextOpenHandler(cost_model=FlatRateCostModel(), instruments=inst,
                            volume_participation=0.05)
        fills, rejs = h.execute([_order(OrderSide.BUY, 100_000)], D1,
                                _section(feed, D1),
                                feed.trading_states(D1), pf)
        assert not rejs and fills[0].qty == 1_500


class TestVppVolumeCapRejection:
    """cap < 一手 → VOLUME_CAP 完全拒单（G10 断言锚点）。"""

    def test_buy_rejected_when_cap_below_lot(self):
        feed, inst = _feed(vol=1_500.0)           # 1,500 × 5% = 75 < 100
        pf = Portfolio(cash=1_000_000.0)
        h = NextOpenHandler(cost_model=FlatRateCostModel(), instruments=inst,
                            volume_participation=0.05)
        fills, rejs = h.execute([_order(OrderSide.BUY, 1_000)], D1,
                                _section(feed, D1),
                                feed.trading_states(D1), pf)
        assert not fills
        assert rejs[0][1].code is RejectCode.VOLUME_CAP

    def test_invalid_rate_rejected(self):
        with pytest.raises(ValueError, match="volume_participation"):
            NextOpenHandler(volume_participation=1.5)
        with pytest.raises(ValueError, match="volume_participation"):
            NextOpenHandler(volume_participation=0.0)
