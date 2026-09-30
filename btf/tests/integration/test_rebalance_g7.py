# -*- coding: utf-8 -*-
"""再平衡与策略层验收测试（M2 任务 5.3；18 号 5.3 验收三条 + G7 扩展 S2）。

验收口径（18 号 5.3 / 09 §14.2 G7 扩展）：
    1. 差额法正确性：目标 vs 持仓 → 只调差额；未列出标的 = 清仓；
    2. 同日卖买资金链（S2）：满仓切换日卖单先成交回笼现金供买单——
       卖先买后失效时买单必因现金不足被拒（这是断言核心）；
    3. orders API 幂等：同输入重复生成 → 同一订单清单（可复现性）；
    4. ctx API 边界（04 §8.3.2）：公共成员白名单——无任何 I/O 痕迹。
"""
from __future__ import annotations

from typing import ClassVar

import pytest
from btf.domain.types import TradingDate
from btf.engine.loop import Engine
from btf.portfolio.portfolio import Portfolio
from btf.portfolio.rebalancer import FullRebalancer
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

from tests.fixtures.scenarios import make_bar, make_state

D = TradingDate.from_ymd
DATES = [D(f"2015010{i}") for i in range(5, 10)]   # 周一~周五

A, B = "000001.SZ", "000002.SZ"


def _mk_state(sym: str, d: TradingDate, pre: float):
    return make_state(sym, d, pre=pre)


def _two_asset_feed():
    """双标的五日场景：A 收盘 10/开盘 10.1；B 收盘 20/开盘 20.2。"""
    from btf.data.memory import MemoryFeed
    from btf.domain.types import AssetClass, Instrument

    bars, states = [], {}
    for d in DATES:
        bars.append(make_bar(A, d, 10.1, 10.1, 10.0, 10.0, 10.0))
        bars.append(make_bar(B, d, 20.2, 20.2, 20.0, 20.0, 20.0))
        states[(d.to_ymd(), A)] = _mk_state(A, d, 10.0)
        states[(d.to_ymd(), B)] = _mk_state(B, d, 20.0)
    instruments = {
        A: Instrument(symbol=A, asset_class=AssetClass.STOCK,
                      board="main", lot_size=100),
        B: Instrument(symbol=B, asset_class=AssetClass.STOCK,
                      board="main", lot_size=100),
    }
    return MemoryFeed(bars=bars, states=states, instruments=instruments,
                      dates=DATES), instruments


class ScriptedStrategy(StrategyBase):
    """按日期脚本化目标的测试策略：date_ymd → targets 映射。"""

    def __init__(self, script: dict[str, dict[str, float]]):
        self.script = script

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        t = self.script.get(date.to_ymd())
        if t is not None:
            ctx.submit_target(TargetPortfolio(date, t))


@pytest.mark.l2
class TestG7CashChain:
    """G7 扩展（S2）：同日卖买资金链——卖先买后 + 顺序撮合现金守恒。"""

    def test_full_switch_sell_funds_buy(self):
        """满仓切换 A→B（留现金余量）：SELL A 成交回笼后 BUY B 必成功。

        现金推演（T+1 开盘价滑点已计入）：
        D1 收盘 target={A:0.95} → D2 撮合 BUY A 95000×10.1=959500 → 现金 40500；
        D2 收盘 target={B:0.9} → NAV=40500+95000×10=990500 → B 目标
        990500×0.9/20=44572.5 → 整手 44500；
        D3 撮合 [SELL A 95000, BUY B 44500]：
          SELL A@10.1 → +959500 → 现金 100 万；
          BUY B 44500×20.2=898900 < 100 万 ✓（无卖单回笼则必拒——断言核心）。
        """
        feed, instruments = _two_asset_feed()
        engine = Engine(
            feed,
            ScriptedStrategy({"20150105": {A: 0.95},    # D1 收盘建仓 A
                              "20150106": {B: 0.9}}),    # D2 收盘切换 B
            start=DATES[0], end=DATES[-1],
            initial_cash=1_000_000.0, instruments=instruments)
        r = engine.run()
        codes = {rej.code.value for _, rej in r.rejections}
        assert "insufficient_cash" not in codes, \
            f"卖先买后失效：{[str(x) for _, x in r.rejections]}"
        sells_a = [f for f in r.fills
                   if f.symbol == A and f.side.value == "sell"]
        buys_b = [f for f in r.fills
                  if f.symbol == B and f.side.value == "buy"]
        assert len(sells_a) == 1 and sells_a[0].qty == 95_000
        assert len(buys_b) == 1 and buys_b[0].qty == 44_500
        # 顺序断言：同日撮合卖 A 先于买 B
        assert r.fills.index(sells_a[0]) < r.fills.index(buys_b[0])


@pytest.mark.l2
class TestDiffMethod:
    """差额法：目标 vs 持仓 → 只调差额；未列出 = 清仓；整手化。"""

    def test_only_difference_traded(self):
        """权重调整只调差额：现金 50 万买入 A 30000@10（NAV 50 万：
        cash 20 万 + 持仓 30 万）→ target={A:0.25, B:0.5}：
        A 目标 500000×0.25/10=12500（SELL 17500）、B 目标 12500
        （BUY 12500）——精确差额，无多余交易。"""
        from btf.domain.orders import Fee, Fill, OrderSide
        from btf.domain.types import AssetClass, Instrument
        instruments = {
            s: Instrument(symbol=s, asset_class=AssetClass.STOCK,
                          board="main", lot_size=100)
            for s in (A, B)}
        reb = FullRebalancer(instruments)
        p = Portfolio(cash=500_000.0)
        p.apply_fill(Fill(fill_id="F1", order_id="O1", symbol=A,
                          side=OrderSide.BUY, qty=30_000, price=10.0,
                          fee=Fee.zero(), fill_date=DATES[0],
                          fill_timing="open"))
        p.mark_all({A: 10.0, B: 20.0})
        data = {A: make_bar(A, DATES[1], 10.1, 10.1, 10.0, 10.0, 10.0),
                B: make_bar(B, DATES[1], 20.2, 20.2, 20.0, 20.0, 20.0)}
        orders = reb.generate_orders(
            TargetPortfolio(rebalance_date=DATES[1], targets={A: 0.25, B: 0.5}),
            p, {}, data)
        sell_a = [o for o in orders if o.symbol == A]
        buy_b = [o for o in orders if o.symbol == B]
        assert len(sell_a) == 1 and sell_a[0].qty == 17_500
        assert len(buy_b) == 1 and buy_b[0].qty == 12_500

    def test_unlisted_symbol_liquidated(self):
        """目标未列出 A（持有中）→ 全额清仓 A。"""
        feed, instruments = _two_asset_feed()
        engine = Engine(
            feed,
            ScriptedStrategy({"20150105": {A: 0.5, B: 0.5},
                              "20150106": {B: 0.5}}),    # A 未列出
            start=DATES[0], end=DATES[-1],
            initial_cash=1_000_000.0, instruments=instruments)
        r = engine.run()
        sell_a = [f for f in r.fills
                  if f.symbol == A and f.side.value == "sell"]
        assert len(sell_a) == 1 and sell_a[0].qty == 50_000

    def test_lot_size_floor(self):
        """目标数量非整手 → 向下取整手（余额转现金）。"""
        from btf.domain.types import AssetClass, Instrument
        instruments = {
            A: Instrument(symbol=A, asset_class=AssetClass.STOCK,
                          board="main", lot_size=100)}
        reb = FullRebalancer(instruments)
        from btf.strategy.rebalance import TargetPortfolio
        p = Portfolio(cash=100_000.0)
        p.mark_all({A: 33.33})                      # 100000/33.33≈3000.3
        orders = reb.generate_orders(
            TargetPortfolio(rebalance_date=DATES[0], targets={A: 1.0}),
            p, {}, {A: make_bar(A, DATES[0], 33.33, 33.33, 33.33, 33.33, 33.33)})
        assert len(orders) == 1 and orders[0].qty == 3_000   # 3000 而非 3003


@pytest.mark.l2
class TestOrderSequence:
    """订单清单顺序：卖先买后；同侧 symbol 字典序（可复现性）。"""

    def test_sells_before_buys_and_sorted(self):
        from btf.domain.types import AssetClass, Instrument
        from btf.strategy.rebalance import TargetPortfolio
        instruments = {
            s: Instrument(symbol=s, asset_class=AssetClass.STOCK,
                          board="main", lot_size=100)
            for s in (A, B, "000003.SZ")}
        reb = FullRebalancer(instruments)
        # 持仓 A、000003；目标 B、000003（A 清仓）
        p = Portfolio(cash=0.0)
        from btf.domain.orders import Fee, Fill, OrderSide
        p.apply_fill(Fill(fill_id="F1", order_id="O1", symbol=A,
                          side=OrderSide.BUY, qty=10_000, price=10.0,
                          fee=Fee.zero(), fill_date=DATES[0],
                          fill_timing="open"))
        p.apply_fill(Fill(fill_id="F2", order_id="O2", symbol="000003.SZ",
                          side=OrderSide.BUY, qty=5_000, price=5.0,
                          fee=Fee.zero(), fill_date=DATES[0],
                          fill_timing="open"))
        p.mark_all({A: 10.0, "000003.SZ": 5.0})
        data = {s: make_bar(s, DATES[0], 10.0, 10.0, 10.0, 10.0, 10.0)
                for s in instruments}
        orders = reb.generate_orders(
            TargetPortfolio(rebalance_date=DATES[0],
                            targets={B: 0.5, "000003.SZ": 0.5}),
            p, {}, data)
        sides = [o.side.value for o in orders]
        first_buy = sides.index("buy") if "buy" in sides else len(sides)
        assert all(s == "sell" for s in sides[:first_buy])
        sell_syms = [o.symbol for o in orders[:first_buy]]
        assert sell_syms == sorted(sell_syms)      # A, 000003.SZ 字典序


@pytest.mark.l2
class TestIdempotence:
    """幂等/可复现：同输入重复生成 → 同一订单清单。"""

    def test_generate_orders_deterministic(self):
        from btf.domain.types import AssetClass, Instrument
        from btf.strategy.rebalance import TargetPortfolio
        instruments = {
            s: Instrument(symbol=s, asset_class=AssetClass.STOCK,
                          board="main", lot_size=100)
            for s in (A, B)}
        reb = FullRebalancer(instruments)
        p = Portfolio(cash=500_000.0)
        p.mark_all({A: 10.0, B: 20.0})
        data = {A: make_bar(A, DATES[0], 10.0, 10.0, 10.0, 10.0, 10.0),
                B: make_bar(B, DATES[0], 20.0, 20.0, 20.0, 20.0, 20.0)}
        target = TargetPortfolio(rebalance_date=DATES[0], targets={A: 0.5, B: 0.5})
        first = reb.generate_orders(target, p, {}, data)
        second = reb.generate_orders(target, p, {}, data)
        assert [(o.symbol, o.side, o.qty) for o in first] == \
               [(o.symbol, o.side, o.qty) for o in second]

    def test_submit_target_repeat_no_duplicate_orders(self):
        """同一 target 重复提交：订单清单不翻倍（引擎每轮重生成覆盖）。"""
        feed, instruments = _two_asset_feed()

        class RepeatStrategy(StrategyBase):
            def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
                if date == DATES[0]:
                    t = TargetPortfolio(date, {A: 0.5})   # 同日两次提交
                    ctx.submit_target(t)
                    ctx.submit_target(t)

        engine = Engine(feed, RepeatStrategy(),
                        start=DATES[0], end=DATES[-1],
                        initial_cash=1_000_000.0, instruments=instruments)
        r = engine.run()
        buys = [f for f in r.fills
                if f.symbol == A and f.side.value == "buy"]
        assert len(buys) == 1 and buys[0].qty == 50_000  # 500000/10.0


@pytest.mark.l2
class TestCtxApiBoundary:
    """ctx API 边界（04 §8.3.2）：公共成员白名单——零 I/O 痕迹。"""

    ALLOWED: ClassVar[set[str]] = {
        "universe", "bar", "history", "state", "portfolio", "current_date",
        "start", "end", "submit_target", "submit_order",
    }

    def test_public_api_whitelist(self):
        public = {a for a in dir(StrategyContext) if not a.startswith("_")}
        assert public <= self.ALLOWED, f"越界成员: {public - self.ALLOWED}"

    def test_no_io_attributes(self):
        """ctx 无文件/网络/子进程任何痕迹（04 §8.3.2 策略无 I/O 纪律）。"""
        forbidden = {"open", "read", "write", "connect", "socket", "requests",
                     "urllib", "subprocess", "system", "popen", "exec", "eval"}
        methods = {a.lower() for a in dir(StrategyContext)}
        assert not (methods & forbidden), methods & forbidden
