# -*- coding: utf-8 -*-
"""L1 单测：NextOpenHandler 撮合与拒单规则（PoC-2 任务 2.4 验收）。

验收锚点：拒单六 code 各一单测；涨跌停浮点容差 1e-4。
"""
from __future__ import annotations

import pytest
from btf.domain.orders import Fee, Fill, Order, OrderSide, OrderType, RejectCode
from btf.domain.types import TradingDate
from btf.execution.cost import FlatRateCostModel, ZeroCostModel
from btf.execution.handler import LIMIT_PRICE_TOL, NextOpenHandler
from btf.portfolio.portfolio import Portfolio

from tests.fixtures.scenarios import (
    D1,
    D2,
    D3,
    D5,
    limit_scenario,
    suspend_scenario,
    t_plus1_scenario,
)

pytestmark = [pytest.mark.l1]


def _buy(symbol: str, qty: int = 100, oid: str = "O00000001") -> Order:
    return Order(order_id=oid, symbol=symbol, side=OrderSide.BUY,
                 order_type=OrderType.MARKET, qty=qty, limit_price=None, created_at=D1)


def _sell(symbol: str, qty: int = 100, oid: str = "O00000002") -> Order:
    return Order(order_id=oid, symbol=symbol, side=OrderSide.SELL,
                 order_type=OrderType.MARKET, qty=qty, limit_price=None, created_at=D1)


def _section(scenario, day: TradingDate) -> dict:
    """取某日截面 map。"""
    for d, section in scenario.feed.bars(None, day, day):
        assert d == day
        return dict(section)
    raise AssertionError(f"{day} 无截面")


class TestRejections:
    """拒单六 code（各一）：顺序与语义锚定。"""

    def test_suspended(self):
        """① SUSPENDED：停牌日无 bar → 拒。"""
        sc = suspend_scenario()
        pf = Portfolio(cash=100_000.0)
        h = NextOpenHandler(instruments=sc.instruments)
        fills, rejs = h.execute([_buy("000002.SZ")], D3,
                                _section(sc, D3), sc.feed.trading_states(D3), pf)
        assert not fills
        assert rejs[0][1].code is RejectCode.SUSPENDED

    def test_limit_up(self):
        """② LIMIT_UP：D2 开盘 11.0 = 涨停价 → 买入拒。"""
        sc = limit_scenario()
        pf = Portfolio(cash=100_000.0)
        h = NextOpenHandler(instruments=sc.instruments)
        fills, rejs = h.execute([_buy("600000.SH")], D2,
                                _section(sc, D2), sc.feed.trading_states(D2), pf)
        assert not fills
        assert rejs[0][1].code is RejectCode.LIMIT_UP

    def test_limit_down(self):
        """③ LIMIT_DOWN：D3 开盘 9.9 = 跌停价 → 卖出拒（先有持仓）。"""
        sc = limit_scenario()
        pf = Portfolio(cash=100_000.0)
        h = NextOpenHandler(instruments=sc.instruments)
        # D1 买入建仓（正常日）
        fills, _ = h.execute([_buy("600000.SH")], D1,
                             _section(sc, D1), sc.feed.trading_states(D1), pf)
        assert fills and pf.position("600000.SH").qty == 100
        pf.advance_day()
        # D3 跌停日卖出 → 拒
        fills, rejs = h.execute([_sell("600000.SH")], D3,
                                _section(sc, D3), sc.feed.trading_states(D3), pf)
        assert not fills
        assert rejs[0][1].code is RejectCode.LIMIT_DOWN

    def test_lot_size(self):
        """④ LOT_SIZE：买单 150 股非整手 → 拒（卖出零股不受限）。"""
        sc = t_plus1_scenario()
        pf = Portfolio(cash=100_000.0)
        h = NextOpenHandler(instruments=sc.instruments)
        _, rejs = h.execute([_buy("000001.SZ", qty=150)], D2,
                            _section(sc, D2), sc.feed.trading_states(D2), pf)
        assert rejs[0][1].code is RejectCode.LOT_SIZE

    def test_t_plus_1(self):
        """⑤ T_PLUS_1：当日买入当日卖（t_plus=1 股票）→ 卖单拒；
        同一 execute 内顺序处理：买单已入账但 available=0。"""
        sc = t_plus1_scenario()
        pf = Portfolio(cash=100_000.0)
        h = NextOpenHandler(instruments=sc.instruments)
        fills, rejs = h.execute([_buy("000001.SZ"), _sell("000001.SZ")], D2,
                                _section(sc, D2), sc.feed.trading_states(D2), pf)
        assert len(fills) == 1                      # 买单成交
        assert pf.position("000001.SZ").qty == 100
        assert [r[1].code for r in rejs] == [RejectCode.T_PLUS_1]

    def test_insufficient_cash(self):
        """⑥ INSUFFICIENT_CASH：现金不足（含费用预估）→ 拒。"""
        sc = t_plus1_scenario()
        pf = Portfolio(cash=500.0)                  # 100 股 @10.1 需 1010+
        h = NextOpenHandler(instruments=sc.instruments)
        fills, rejs = h.execute([_buy("000001.SZ")], D2,
                                _section(sc, D2), sc.feed.trading_states(D2), pf)
        assert not fills
        assert rejs[0][1].code is RejectCode.INSUFFICIENT_CASH


class TestNextOpenFill:
    def test_fill_at_open_price_with_fee(self):
        """成交价=开盘价（无滑点）；费用入账；守恒式成立。"""
        sc = t_plus1_scenario()
        pf = Portfolio(cash=10_000.0)
        cost = FlatRateCostModel()
        h = NextOpenHandler(cost_model=cost, instruments=sc.instruments)
        fills, rejs = h.execute([_buy("000001.SZ")], D2,
                                _section(sc, D2), sc.feed.trading_states(D2), pf)
        assert not rejs and len(fills) == 1
        f = fills[0]
        assert f.price == 10.1 and f.fill_timing == "open" and f.fill_date == D2
        # 手算：turnover=1010 → comm=max(0.2525,5)=5；transfer=0.0101→0.01
        assert f.fee.commission == 5.0
        assert f.fee.stamp_duty == 0.0
        assert f.fee.total == f.fee.commission + f.fee.transfer_fee
        assert pf.cash == 10_000.0 - (10.1 * 100 + f.fee.total)

    def test_sequential_same_day_cash_reuse(self):
        """同日卖先买后：卖出回笼资金可供后续买单（顺序处理语义）。"""
        sc = t_plus1_scenario()
        pf = Portfolio(cash=1_000.0)               # 预置持仓的建仓成本
        # D1 手工入账持仓（现金 −1000 → 0）
        pf.apply_fill(Fill(
            fill_id="F00000000", order_id="O0", symbol="000001.SZ",
            side=OrderSide.BUY, qty=100, price=10.0, fee=Fee.zero(),
            fill_date=D1, fill_timing="open"))
        assert pf.cash == 0.0
        pf.advance_day()
        h = NextOpenHandler(instruments=sc.instruments)
        # 现金 0：先卖 100 股回笼 ~1010，再买 100 股 ~1010 → 两单都成
        fills, rejs = h.execute([_sell("000001.SZ"), _buy("000001.SZ")], D2,
                                _section(sc, D2), sc.feed.trading_states(D2), pf)
        assert len(fills) == 2 and not rejs

    def test_t0_etf_same_day_roundtrip(self):
        """T+0 货币 ETF：当日买入立即可卖（E2 规则）。"""
        sc = t_plus1_scenario()
        pf = Portfolio(cash=100_000.0)
        h = NextOpenHandler(instruments=sc.instruments)
        fills, rejs = h.execute([_buy("511380.SH", oid="O1"), _sell("511380.SH", oid="O2")],
                                D2, _section(sc, D2), sc.feed.trading_states(D2), pf)
        assert len(fills) == 2 and not rejs
        assert pf.position("511380.SH") is None     # 空仓移除

    def test_fill_id_sequential(self):
        sc = t_plus1_scenario()
        pf = Portfolio(cash=100_000.0)
        h = NextOpenHandler(instruments=sc.instruments, fill_seq_start=7)
        fills, _ = h.execute([_buy("000001.SZ", oid="O1"), _buy("511380.SH", oid="O2")],
                             D2, _section(sc, D2), sc.feed.trading_states(D2), pf)
        assert [f.fill_id for f in fills] == ["F00000008", "F00000009"]

    def test_zero_cost_baseline(self):
        """零费用基线：Fee.zero，现金变化恰等于成交额。"""
        sc = t_plus1_scenario()
        pf = Portfolio(cash=100_000.0)
        h = NextOpenHandler(cost_model=ZeroCostModel(), instruments=sc.instruments)
        fills, _ = h.execute([_buy("000001.SZ")], D2,
                             _section(sc, D2), sc.feed.trading_states(D2), pf)
        assert fills[0].fee.total == 0.0
        assert pf.cash == 100_000.0 - 10.1 * 100


class TestLimitPriceTolerance:
    """涨跌停浮点容差 1e-4（计划 2.4 验收）。"""

    def _run(self, open_price: float, side: OrderSide) -> bool:
        """构造 limit=11.0、给定开盘价的合成截面；返回是否拒单。"""
        from tests.fixtures.scenarios import make_bar, make_state

        bar = make_bar("600000.SH", D5, o=open_price, h=open_price,
                       lo=open_price, c=open_price, pre=10.0)
        state = make_state("600000.SH", D5, pre=10.0)   # limit_up=11.0
        assert state.limit_up_price == 11.0
        pf = Portfolio(cash=100_000.0)
        h = NextOpenHandler()
        order = _buy("600000.SH") if side is OrderSide.BUY else _sell("600000.SH")
        _, rejs = h.execute([order], D5, {"600000.SH": bar}, {"600000.SH": state}, pf)
        return bool(rejs)

    def test_at_limit_rejected(self):
        assert self._run(11.0, OrderSide.BUY)                       # 恰涨停

    def test_within_tolerance_rejected(self):
        # open = 11.0×(1−0.5e-4) = 10.99945 ≥ 11.0×(1−1e-4) → 容差内仍拒
        assert self._run(11.0 * (1 - 0.5 * LIMIT_PRICE_TOL), OrderSide.BUY)

    def test_outside_tolerance_fills(self):
        # open = 11.0×(1−2e-4) = 10.9978 < 阈值 → 正常成交
        assert not self._run(11.0 * (1 - 2 * LIMIT_PRICE_TOL), OrderSide.BUY)

    def test_sell_side_down_limit_symmetry(self):
        """跌停卖出对称：open=9.0（limit_down）拒；略高不拒。"""
        from tests.fixtures.scenarios import make_bar, make_state

        def run(open_price: float) -> bool:
            bar = make_bar("600000.SH", D5, o=open_price, h=open_price,
                           lo=open_price, c=open_price, pre=10.0)
            state = make_state("600000.SH", D5, pre=10.0)   # limit_down=9.0
            pf = Portfolio(cash=100_000.0)
            pf.apply_fill(Fill(
                fill_id="F0", order_id="O0", symbol="600000.SH", side=OrderSide.BUY,
                qty=100, price=10.0, fee=Fee.zero(),
                fill_date=D1, fill_timing="open"))
            pf.advance_day()
            h = NextOpenHandler()
            _, rejs = h.execute([_sell("600000.SH")], D5,
                                {"600000.SH": bar}, {"600000.SH": state}, pf)
            return bool(rejs)

        assert run(9.0)                                   # 恰跌停 → 拒
        assert not run(9.0 * (1 + 2 * LIMIT_PRICE_TOL))   # 容差外 → 成交
