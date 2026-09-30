# -*- coding: utf-8 -*-
"""L2 集成测试：引擎主循环十步骤时序（PoC-2 任务 2.5 验收）。

验收锚点：
    - 时序单测：步骤顺序断言（事件日志顺序——每交易日
      MarketOpen → Fill*/Rejected* → MarketClose → Submitted* → SessionEnd）；
    - 前视封堵：ctx.history 未来日期访问触发运行时断言；
    - 端到端闭环：T+1 买卖全链路（目标→订单→撮合→入账→快照）。
"""
from __future__ import annotations

import pytest
from btf.domain.types import TradingDate
from btf.engine.loop import Engine
from btf.execution.cost import ZeroCostModel
from btf.execution.handler import NextOpenHandler
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

from tests.fixtures.scenarios import (
    D1,
    D3,
    D5,
    limit_scenario,
    t_plus1_scenario,
)

pytestmark = [pytest.mark.l2]


class ScriptedStrategy(StrategyBase):
    """按日期脚本化目标：{ymd: {symbol: weight}}。"""

    def __init__(self, script: dict[str, dict[str, float]]):
        self.script = script
        self.close_dates: list[str] = []
        self.open_dates: list[str] = []

    def on_open(self, ctx: StrategyContext, date: TradingDate) -> None:
        self.open_dates.append(date.to_ymd())

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        self.close_dates.append(date.to_ymd())
        targets = self.script.get(date.to_ymd())
        if targets is not None:
            ctx.submit_target(TargetPortfolio(rebalance_date=date, targets=targets))


class CountingFeed:
    """trading_states 计数包装（2.8 第二刀验收：ctx states 惰性装载）。"""

    def __init__(self, feed):
        self._feed = feed
        self.states_calls = 0

    def bars(self, symbols, start, end):
        return self._feed.bars(symbols, start, end)

    def bars_of(self, symbol, start, end):
        return self._feed.bars_of(symbol, start, end)

    def corporate_actions(self, start, end):
        return self._feed.corporate_actions(start, end)

    def trading_states(self, date, symbols=None, *, with_touch_flags=True):
        self.states_calls += 1
        return self._feed.trading_states(date, symbols, with_touch_flags=with_touch_flags)


class TestEngineLoop:
    def test_full_cycle_t_plus1(self, tmp_path):
        """端到端：D1 目标 50% → D2 开盘买入 → D2 清仓目标 → D3 开盘卖出。

        ZeroCost：现金守恒可手算。事件序=时序验收锚点。
        """
        sc = t_plus1_scenario()
        strategy = ScriptedStrategy({
            "20150105": {"000001.SZ": 0.5},   # D1 收盘决策
            "20150106": {},                    # D2 收盘清仓
        })
        engine = Engine(
            sc.feed, strategy, start=D1, end=D5,
            initial_cash=100_000.0,
            handler=NextOpenHandler(cost_model=ZeroCostModel(),
                                    instruments=sc.instruments),
            instruments=sc.instruments,
            event_log_path=tmp_path / "events.jsonl",
        )
        r = engine.run()

        # 撮合：D2 买 5000 股@10.1；D3 卖 5000 股@10.2
        assert len(r.fills) == 2
        buy, sell = r.fills
        assert (buy.symbol, buy.side.name, buy.qty, buy.price) == ("000001.SZ", "BUY", 5000, 10.1)
        assert (sell.symbol, sell.side.name, sell.qty, sell.price) == ("000001.SZ", "SELL", 5000, 10.2)
        assert [o.order_id for o in r.orders] == ["O00000001", "O00000002"]
        assert not r.rejections

        # 现金：100000 − 50500 + 51000 = 100500（ZeroCost）
        assert r.snapshots[-1].cash == pytest.approx(100_500.0)
        assert r.snapshots[-1].total_value == pytest.approx(100_500.0)
        assert r.snapshots[1].market_value == pytest.approx(5000 * 10.1)   # D2 收盘

        # 回放事件日志：时序锚点
        from btf.engine.events_log import EventLogReader
        kinds = [type(e).__name__ for e in EventLogReader(tmp_path / "events.jsonl").events()]
        # D1: open, close, submitted, session_end；D2: open, fill, close, submitted,
        #     session_end；D3: open, fill, close, session_end；D4/D5: open, close, session_end
        assert kinds == [
            "MarketOpenEvent", "MarketCloseEvent", "OrderSubmittedEvent", "SessionEndEvent",
            "MarketOpenEvent", "FillEvent", "MarketCloseEvent", "OrderSubmittedEvent",
            "SessionEndEvent",
            "MarketOpenEvent", "FillEvent", "MarketCloseEvent", "SessionEndEvent",
            "MarketOpenEvent", "MarketCloseEvent", "SessionEndEvent",
            "MarketOpenEvent", "MarketCloseEvent", "SessionEndEvent",
        ]

    def test_limit_up_rejection_path(self, tmp_path):
        """涨停拒单：D2 开盘=涨停价 11.0 → 买拒；D3 跌停开盘 9.9 → 买单成交。"""
        sc = limit_scenario()
        strategy = ScriptedStrategy({"20150105": {"600000.SH": 0.5}})
        engine = Engine(
            sc.feed, strategy, start=D1, end=D5,
            initial_cash=100_000.0,
            handler=NextOpenHandler(cost_model=ZeroCostModel(),
                                    instruments=sc.instruments),
            instruments=sc.instruments,
            event_log_path=tmp_path / "events.jsonl",
        )
        r = engine.run()

        # D2 买 5000@11.0 → LIMIT_UP 拒；策略脚本只有 D1 目标，无后续补单
        assert len(r.rejections) == 1
        order, reason = r.rejections[0]
        assert reason.code.value == "limit_up"
        assert order.qty == 5000

        from btf.engine.events_log import EventLogReader
        kinds = [type(e).__name__ for e in EventLogReader(tmp_path / "events.jsonl").events()]
        day2 = kinds[4:8]     # D2 段
        assert "OrderRejectedEvent" in day2 and "FillEvent" not in day2

    def test_lookahead_block(self):
        """前视封堵：history(end=未来) → 运行时断言（2.5 验收）。"""
        sc = t_plus1_scenario()

        class PeekingStrategy(StrategyBase):
            def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
                ctx.history("000001.SZ", D5, n_bars=1)   # D5 > 当日

        engine = Engine(sc.feed, PeekingStrategy(), start=D1, end=D3,
                        initial_cash=100_000.0, instruments=sc.instruments)
        with pytest.raises(RuntimeError, match="前视封堵"):
            engine.run()

    def test_history_within_current_ok(self):
        """end ≤ 当前日合法（含当日）；缓存跨日共享（同标的一次装载）。"""
        sc = t_plus1_scenario()
        seen: list[int] = []

        class HistStrategy(StrategyBase):
            def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
                bars = ctx.history("000001.SZ", date, n_bars=2)
                seen.append(len(bars))

        engine = Engine(sc.feed, HistStrategy(), start=D1, end=D3,
                        initial_cash=100_000.0, instruments=sc.instruments)
        engine.run()
        assert seen == [1, 2, 2]     # D1 首日仅 1 根；D2/D3 各 2 根

    def test_event_log_off_by_default(self):
        """不传 event_log_path → 不落盘（轻量路径）。"""
        sc = t_plus1_scenario()
        r = Engine(sc.feed, ScriptedStrategy({}), start=D1, end=D3,
                   initial_cash=100_000.0, instruments=sc.instruments).run()
        assert r.events_path is None
        assert r.n_days == 3

    def test_no_orders_no_market_events_spurious(self):
        """空策略：每日恰好 4 事件（open/close/session_end + 无订单）。"""
        sc = t_plus1_scenario()
        engine = Engine(sc.feed, ScriptedStrategy({}), start=D1, end=D5,
                        initial_cash=100_000.0, instruments=sc.instruments)
        r = engine.run()
        assert not r.fills and not r.orders and not r.rejections
        assert len(r.snapshots) == 5
        assert all(s.total_value == 100_000.0 for s in r.snapshots)   # 空仓 NAV 不变
        assert r.snapshots[0].weights["@CASH"] == 1.0

    def test_states_lazy_not_queried(self):
        """惰性化（2.8 第二刀）：策略不查状态 → trading_states 零调用。

        旧实现 on_open 每日无条件物化（5 日=5 次）；撮合/Rebalancer 路径
        在无订单/无目标时本就不触发——空策略下计数=0 即 ctx 路径豁免证明。
        """
        sc = t_plus1_scenario()
        feed = CountingFeed(sc.feed)
        Engine(feed, ScriptedStrategy({}), start=D1, end=D5,
               initial_cash=100_000.0, instruments=sc.instruments).run()
        assert feed.states_calls == 0

    def test_states_lazy_queried_once_per_day(self):
        """策略查询状态 → 当日 ctx 恰好 1 次装载（on_open/on_close 共享物化）。"""
        sc = t_plus1_scenario()
        feed = CountingFeed(sc.feed)
        seen: list[tuple[str, float]] = []

        class StateProbe(StrategyBase):
            def on_open(self, ctx, date):
                st = ctx.state("000001.SZ")
                if st is not None:
                    seen.append((date.to_ymd(), st.limit_up_price))

            def on_close(self, ctx, date):
                st = ctx.state("000001.SZ")
                if st is not None:
                    seen.append((date.to_ymd(), st.limit_up_price))
                if date == D1:
                    ctx.submit_target(TargetPortfolio(
                        date, {"000001.SZ": 0.5}))       # D2 开盘建仓

        Engine(feed, StateProbe(), start=D1, end=D3,
               initial_cash=100_000.0, instruments=sc.instruments).run()
        # D1 空仓：ctx 标的集合=∅ → state 缺席（与旧实现语义一致）；
        # D2 起持仓 000001.SZ：on_open/on_close 各查一次、共享当日单次物化
        assert len(seen) == 4
        assert [d for d, _ in seen] == ["20150106", "20150106",
                                        "20150107", "20150107"]
        assert [up for _, up in seen] == pytest.approx([11.0, 11.0, 11.11, 11.11])
        # 调用计（5 次）：D1 ctx 惰性 1（空集装载）+ D1 Rebalancer 1（目标
        # 已提交）+ D2 撮合 1（拒单判定，必要）+ D2/D3 ctx 惰性各 1。
        # 惰性豁免点：D3 撮合（无 pending）不调；D2/D3 的 on_open+on_close
        # 双查询共享单次物化（旧实现每日无条件 +1）。
        assert feed.states_calls == 5
