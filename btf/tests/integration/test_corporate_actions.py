# -*- coding: utf-8 -*-
"""L2 集成测试：公司行动两时点正确性（PoC-3 任务 3.2/3.3/3.4 验收）。

G1 五案例（18 号计划 3.2：送转/派息/混合/老数据 ex≠pay/pay 缺失回退），
全部数值=独立手算（公式唯一出处 04 §8.2.6 S1 规格）：
    a 纯送转 10送10：qty×2、avg_cost/2、除权价/2 → 单日 Δ(现金+MV)=0
    b 纯派息 pay≡ex：avg_cost−1、cash+100 → 单日 Δ=0
    c 混合 ex≠pay：avg_cost=(10−1)/2、ex 日 Δ=−100（除息缺口）、
      pay 日 Δ=+100 → 窗口合并 Δ=0
    d 纯派息 ex≠pay + 窗口内卖 60 股（C1 案例）：pay 仍按 qty_at_ex=100
      到账（登记分红权不被卖出剥夺）
    e pay_date 缺失回退（E5）：ex 当日到账 ≡ 案例 b

实现注意（09 §14.2 终审）：
    ① 送转日 available_qty 同步 ×(1+stk_div)——除权次日可卖不被低估
    ② qty_at_ex 跨窗口 pending 登记：清仓后 pay 日仍按登记数量发放

事件时序锚点（06 §10.1 步骤①）：CorporateActionEvent 先于 MarketOpenEvent；
ex/pay 两时点各留一条（04 §8.4；pay≡ex 重合日=两条）。
"""
from __future__ import annotations

import pytest
from btf.data.memory import MemoryFeed
from btf.domain.action import CorporateAction
from btf.domain.market import Bar, TradingState
from btf.domain.orders import Order, OrderSide, OrderType
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.engine.events_log import EventLogReader
from btf.engine.loop import Engine
from btf.execution.cost import ZeroCostModel
from btf.execution.handler import NextOpenHandler
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

pytestmark = [pytest.mark.l2]

SYMBOL = "600900.SH"
CASH = 1_000.0
_INSTRUMENTS = {SYMBOL: Instrument(symbol=SYMBOL, asset_class=AssetClass.STOCK,
                                   board="main", lot_size=100)}
# 六个交易日：2016-06-01(三) 02(四) 03(五) 06(一) 07(二) 08(三)
T1 = TradingDate.from_iso(2016, 6, 1)
T2 = TradingDate.from_iso(2016, 6, 2)
T3 = TradingDate.from_iso(2016, 6, 3)
T4 = TradingDate.from_iso(2016, 6, 6)
T5 = TradingDate.from_iso(2016, 6, 7)
T6 = TradingDate.from_iso(2016, 6, 8)
DATES = [T1, T2, T3, T4, T5, T6]


def _action(ex, pay, cash_div, stk_div, symbol=SYMBOL):
    return CorporateAction(
        symbol=symbol, ex_date=ex, pay_date=pay, record_date=None,
        cash_div_per_share=cash_div, stk_div_per_share=stk_div, ann_date=None,
    )


def _bar(day, o, c, pre) -> Bar:
    return Bar(symbol=SYMBOL, date=day, open=o, high=max(o, c),
               low=min(o, c), close=c, volume=1_000_000.0,
               amount=c * 1_000_000.0, pre_close=pre)


def _state(day, pre) -> TradingState:
    return TradingState(
        symbol=SYMBOL, date=day,
        limit_up_price=round(pre * 1.1, 2), limit_down_price=round(pre * 0.9, 2),
        is_suspended=False, is_st=False, is_delisted=False,
        is_limit_up=False, is_limit_down=False,
    )


def _feed(bars, actions) -> MemoryFeed:
    states = {(d.to_ymd(), SYMBOL): _state(d, _bar_pre(bars, d)) for d in DATES}
    return MemoryFeed(list(bars), states, _INSTRUMENTS,
                      dates=DATES, actions=actions)


def _bar_pre(bars, day) -> float:
    """当日 bar 的 pre_close（状态面板基准）；无 bar 日沿用前值。"""
    prev = 10.0
    for b in bars:
        if b.date == day:
            return b.pre_close
        if b.date < day:
            prev = b.close
    return prev


class BuyHoldSellStrategy(StrategyBase):
    """T1 收盘全仓买入；可选显式卖单 {ymd: qty}（零股卖出=一次性了结）。"""

    def __init__(self, sells: dict[str, int] | None = None):
        self.sells = sells or {}
        self.bought = False

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        ymd = date.to_ymd()
        if ymd == T1.to_ymd() and not self.bought:
            ctx.submit_target(
                TargetPortfolio(rebalance_date=date, targets={SYMBOL: 1.0}))
        if ymd in self.sells:
            ctx.submit_order(Order(
                order_id="", symbol=SYMBOL, side=OrderSide.SELL,
                order_type=OrderType.MARKET, qty=self.sells[ymd],
                limit_price=None, created_at=date,
            ))


def _run(feed, strategy, tmp_path):
    return Engine(
        feed, strategy, start=T1, end=T6, initial_cash=CASH,
        handler=NextOpenHandler(cost_model=ZeroCostModel(),
                                instruments=_INSTRUMENTS),
        instruments=_INSTRUMENTS,
        event_log_path=tmp_path / "events.jsonl",
    ).run()


def _snapshot_by_day(result):
    return {s.date.to_ymd(): s for s in result.snapshots}


def _ca_events(result):
    """事件日志中的 (发生日, action) 列表（按 seq 序）。

    发生日按事件流上下文归属：CA 事件先于 MarketOpen → 取**其后最近**
    MarketOpenEvent 的日期（payload 无时点字段——04 §8.4 签名冻结，
    ex/pay 同 payload 各一条靠 seq 上下文区分）。
    """
    reader = EventLogReader(result.events_path)
    from btf.domain.events import CorporateActionEvent, MarketOpenEvent
    events = list(reader.events())
    out = []
    batch: list = []          # 尚未归属的 CA（等待下一个 MarketOpen）
    for e in events:
        if isinstance(e, CorporateActionEvent):
            batch.append(e.action)
        elif isinstance(e, MarketOpenEvent):
            out.extend((e.date.to_ymd(), a) for a in batch)
            batch.clear()
    return out


# ─────────────────────────────────────────────
# 案例 a：纯送转（10 送 10）——单日 Δ(现金+MV)=0
# ─────────────────────────────────────────────
def test_g1_a_pure_stk_div(tmp_path):
    """手算：T2 买 100@10（cash 0）；T3 除权 qty 200、价 5 → MV 1000 不变。

    实现注意①：T3 available_qty 同步 ×2 → 当日可卖 200（T4 成交验证）。
    """
    bars = [
        _bar(T1, 10.0, 10.0, 10.0),
        _bar(T2, 10.0, 10.0, 10.0),        # 买入执行日（100@10）
        _bar(T3, 5.0, 5.0, 5.0),           # 除权日：10送10 价格减半
        _bar(T4, 5.0, 5.0, 5.0),
        _bar(T5, 5.0, 5.0, 5.0),
        _bar(T6, 5.0, 5.0, 5.0),
    ]
    actions = [_action(T3, None, 0.0, 1.0)]
    feed = _feed(bars, actions)
    # T3 收盘卖 200（验证 available_qty 扩张），T4 成交
    strategy = BuyHoldSellStrategy(sells={T3.to_ymd(): 200})
    result = _run(feed, strategy, tmp_path)

    snap = _snapshot_by_day(result)
    # T2：买入 100 股
    assert snap[T2.to_ymd()].positions_qty == {SYMBOL: 100}
    assert snap[T2.to_ymd()].cash == pytest.approx(0.0)
    # T3：送转后 200 股、现金 0、名义 MV=200×5=1000
    assert snap[T3.to_ymd()].positions_qty == {SYMBOL: 200}
    assert snap[T3.to_ymd()].cash == pytest.approx(0.0)
    # 守恒：送转单日 Δ(现金+名义总市值)=0
    assert snap[T3.to_ymd()].total_value == pytest.approx(
        snap[T2.to_ymd()].total_value)
    # 实现注意①：available_qty=200 → T4 全数卖出成交
    sells = [f for f in result.fills if f.side is OrderSide.SELL]
    assert len(sells) == 1 and sells[0].qty == 200
    assert snap[T4.to_ymd()].positions_qty == {}
    assert snap[T4.to_ymd()].cash == pytest.approx(200 * 5.0)
    # 事件：T3 一条（ex 时点；纯送转无 pay）
    ca = _ca_events(result)
    assert len(ca) == 1 and ca[0][0] == T3.to_ymd()
    assert ca[0][1].stk_div_per_share == pytest.approx(1.0)


# ─────────────────────────────────────────────
# 案例 b：纯派息 pay≡ex（2020+ 主流口径）——单日 Δ=0
# ─────────────────────────────────────────────
def test_g1_b_cash_div_pay_eq_ex(tmp_path):
    """手算：T2 买 100@10；T3 派 1 元/股（pay=ex）：avg_cost=9、
    cash+100、除息价 9 → TV=900+100=1000 单日不变；事件两条。"""
    bars = [
        _bar(T1, 10.0, 10.0, 10.0),
        _bar(T2, 10.0, 10.0, 10.0),
        _bar(T3, 9.0, 9.0, 9.0),           # 除息日：10−1
        _bar(T4, 9.0, 9.0, 9.0),
        _bar(T5, 9.0, 9.0, 9.0),
        _bar(T6, 9.0, 9.0, 9.0),
    ]
    actions = [_action(T3, T3, 1.0, 0.0)]
    result = _run(_feed(bars, actions), BuyHoldSellStrategy(), tmp_path)

    snap = _snapshot_by_day(result)
    assert snap[T3.to_ymd()].cash == pytest.approx(100.0)   # qty_at_ex×1
    assert snap[T3.to_ymd()].positions_qty == {SYMBOL: 100}
    # 守恒：单日 Δ=0（pay≡ex 退化单日）
    assert snap[T3.to_ymd()].total_value == pytest.approx(
        snap[T2.to_ymd()].total_value)                      # 100×9+100=1000
    # 事件：ex/pay 两时点各一条（重合日仍两条）
    ca = _ca_events(result)
    assert len(ca) == 2
    assert all(ymd == T3.to_ymd() for ymd, _ in ca)


# ─────────────────────────────────────────────
# 案例 c：混合（送转+派息同日，ex≠pay 老数据）
# ─────────────────────────────────────────────
def test_g1_c_mixed_ex_ne_pay(tmp_path):
    """手算：T2 买 100@10；T3 ex：10送10+派1（登记 100 股）：
    qty=200、avg_cost=(10−1)/2=4.5、除权价 4.5 → TV=900（Δ=−100 缺口）；
    T6 pay：cash+100 → TV=1000（Δ=+100）；窗口合并 Δ=0。"""
    bars = [
        _bar(T1, 10.0, 10.0, 10.0),
        _bar(T2, 10.0, 10.0, 10.0),
        _bar(T3, 4.5, 4.5, 4.5),           # (10−1)/2
        _bar(T4, 4.5, 4.5, 4.5),
        _bar(T5, 4.5, 4.5, 4.5),
        _bar(T6, 4.5, 4.5, 4.5),
    ]
    actions = [_action(T3, T6, 1.0, 1.0)]
    result = _run(_feed(bars, actions), BuyHoldSellStrategy(), tmp_path)

    snap = _snapshot_by_day(result)
    # T3：份额×2、现金不动（pay 未到）
    assert snap[T3.to_ymd()].positions_qty == {SYMBOL: 200}
    assert snap[T3.to_ymd()].cash == pytest.approx(0.0)
    # ex 日 Δ = −qty_at_ex×cash_div = −100（除息缺口，价格恒定前提）
    assert snap[T3.to_ymd()].total_value == pytest.approx(900.0)
    # T4/T5 窗口内：TV 恒 900（NAV 低估披露口径 B1）
    assert snap[T5.to_ymd()].total_value == pytest.approx(900.0)
    # T6 pay：+100 → 合并 Δ=0
    assert snap[T6.to_ymd()].cash == pytest.approx(100.0)
    assert snap[T6.to_ymd()].total_value == pytest.approx(1000.0)
    assert snap[T6.to_ymd()].total_value == pytest.approx(
        snap[T2.to_ymd()].total_value)
    # 事件：T3 一条（ex）、T6 一条（pay）
    ca = _ca_events(result)
    assert [ymd for ymd, _ in ca] == [T3.to_ymd(), T6.to_ymd()]


# ─────────────────────────────────────────────
# 案例 d：ex≠pay + 窗口内卖出 60 股（C1：登记分红权不被剥夺）
# ─────────────────────────────────────────────
def test_g1_d_sell_inside_window(tmp_path):
    """手算：T2 买 100@10；T3 ex 派 1（pay=T6）价 9；T4 决策卖 60 →
    T5 成交 @9.5（cash=570）；T6 pay 按 qty_at_ex=100 到账 100（非 40！）。
    行动贡献守恒：ex 缺口 −100 + pay 到账 +100 = 0。"""
    bars = [
        _bar(T1, 10.0, 10.0, 10.0),
        _bar(T2, 10.0, 10.0, 10.0),
        _bar(T3, 9.0, 9.0, 9.0),           # ex 日（pay=T6）
        _bar(T4, 9.5, 9.5, 9.0),
        _bar(T5, 9.5, 9.5, 9.5),           # 卖 60 成交 @9.5
        _bar(T6, 9.0, 9.0, 9.5),
    ]
    actions = [_action(T3, T6, 1.0, 0.0)]
    strategy = BuyHoldSellStrategy(sells={T4.to_ymd(): 60})
    result = _run(_feed(bars, actions), strategy, tmp_path)

    snap = _snapshot_by_day(result)
    # T5：卖出 60 @9.5 → 持仓 40、cash=570
    assert snap[T5.to_ymd()].positions_qty == {SYMBOL: 40}
    assert snap[T5.to_ymd()].cash == pytest.approx(60 * 9.5)
    # T6：pay 按 qty_at_ex=100 到账（C1 断言：非持仓 40×1）
    assert snap[T6.to_ymd()].cash == pytest.approx(60 * 9.5 + 100.0)
    # 行动贡献守恒（ex 缺口 −100 / pay 到账 +100）：
    #   T3 TV=900（−100）；T6 相对"无行动世界"的 TV 差 = +100
    #   （无行动：T6 TV=40×9+570=930；实际 930+100=1030）
    assert snap[T6.to_ymd()].total_value == pytest.approx(40 * 9.0 + 570.0 + 100.0)


# ─────────────────────────────────────────────
# 案例 e：pay_date 缺失回退（E5：ex 当日到账）
# ─────────────────────────────────────────────
def test_g1_e_pay_missing_fallback(tmp_path):
    """手算：cash_div>0 且 pay_date=None（库内 ~0.1%/55 行）→
    按 ex_date 当日到账（保守回退，与 2020+ 口径一致）。"""
    bars = [
        _bar(T1, 10.0, 10.0, 10.0),
        _bar(T2, 10.0, 10.0, 10.0),
        _bar(T3, 9.0, 9.0, 9.0),
        _bar(T4, 9.0, 9.0, 9.0),
        _bar(T5, 9.0, 9.0, 9.0),
        _bar(T6, 9.0, 9.0, 9.0),
    ]
    actions = [_action(T3, None, 1.0, 0.0)]
    result = _run(_feed(bars, actions), BuyHoldSellStrategy(), tmp_path)

    snap = _snapshot_by_day(result)
    assert snap[T3.to_ymd()].cash == pytest.approx(100.0)
    assert snap[T3.to_ymd()].total_value == pytest.approx(
        snap[T2.to_ymd()].total_value)     # 单日 Δ=0
    ca = _ca_events(result)
    assert len(ca) == 2                    # ex + 回退 pay 各一条


# ─────────────────────────────────────────────
# 实现注意②极端：窗口内清仓，pay 日仍按登记数量发放
# ─────────────────────────────────────────────
def test_pending_survives_full_close(tmp_path):
    """T5 清仓 100 股（无持仓）；T6 pay 仍到账 100（qty_at_ex 跨窗口
    pending 登记——引擎内部登记依据，与 B1"无应收科目"NAV 口径并行不悖）。"""
    bars = [
        _bar(T1, 10.0, 10.0, 10.0),
        _bar(T2, 10.0, 10.0, 10.0),
        _bar(T3, 9.0, 9.0, 9.0),
        _bar(T4, 9.5, 9.5, 9.0),
        _bar(T5, 9.5, 9.5, 9.5),
        _bar(T6, 9.5, 9.5, 9.5),
    ]
    actions = [_action(T3, T6, 1.0, 0.0)]
    strategy = BuyHoldSellStrategy(sells={T4.to_ymd(): 100})
    result = _run(_feed(bars, actions), strategy, tmp_path)

    snap = _snapshot_by_day(result)
    assert snap[T5.to_ymd()].positions_qty == {}          # 已清仓
    cash_after_sell = 100 * 9.5
    assert snap[T5.to_ymd()].cash == pytest.approx(cash_after_sell)
    # T6：空仓仍到账（登记数量 100）
    assert snap[T6.to_ymd()].cash == pytest.approx(cash_after_sell + 100.0)


# ─────────────────────────────────────────────
# 事件时序：CorporateActionEvent 先于 MarketOpenEvent（06 §10.1 步骤①）
# ─────────────────────────────────────────────
def test_ca_event_before_market_open(tmp_path):
    from btf.domain.events import CorporateActionEvent, MarketOpenEvent

    bars = [
        _bar(T1, 10.0, 10.0, 10.0),
        _bar(T2, 10.0, 10.0, 10.0),
        _bar(T3, 9.0, 9.0, 9.0),
        _bar(T4, 9.0, 9.0, 9.0),
        _bar(T5, 9.0, 9.0, 9.0),
        _bar(T6, 9.0, 9.0, 9.0),
    ]
    actions = [_action(T3, T3, 1.0, 0.0)]
    result = _run(_feed(bars, actions), BuyHoldSellStrategy(), tmp_path)
    events = list(EventLogReader(result.events_path).events())
    kinds = [type(e).__name__ for e in events]
    i_open_t3 = next(
        i for i, e in enumerate(events)
        if isinstance(e, MarketOpenEvent) and e.date == T3)
    # T3 段的前两事件均为公司行动（ex→pay），且在 MarketOpen 前
    assert all(isinstance(e, CorporateActionEvent) for e in events[i_open_t3 - 2:i_open_t3])
    assert kinds[i_open_t3 - 2:i_open_t3] == ["CorporateActionEvent"] * 2


# ─────────────────────────────────────────────
# 无持仓标的行动不处理（no-op：不入事件日志、不影响现金）
# ─────────────────────────────────────────────
def test_ca_no_position_noop(tmp_path):
    """未持有标的的 ex/pay 全程 no-op——全市场行动每日数百条，
    无持仓标的不入事件日志（50 万事件预算纪律）。"""
    other = "000001.SZ"
    bars = [
        _bar(T1, 10.0, 10.0, 10.0),
        _bar(T2, 10.0, 10.0, 10.0),
        _bar(T3, 10.0, 10.0, 10.0),
        _bar(T4, 10.0, 10.0, 10.0),
        _bar(T5, 10.0, 10.0, 10.0),
        _bar(T6, 10.0, 10.0, 10.0),
    ]
    actions = [
        CorporateAction(
            symbol=other, ex_date=T3, pay_date=T6, record_date=None,
            cash_div_per_share=5.0, stk_div_per_share=2.0, ann_date=None),
    ]
    # 策略不买（无任何持仓）
    class Noop(StrategyBase):
        def on_close(self, ctx, date): ...

    feed = _feed(bars, actions)
    result = _run(feed, Noop(), tmp_path)
    assert _ca_events(result) == []
    snap = _snapshot_by_day(result)
    assert all(s.cash == pytest.approx(CASH) for s in snap.values())


__all__ = [
    "T1", "T2", "T3", "T4", "T5", "T6",
    "BuyHoldSellStrategy", "_action", "_bar", "_feed", "_run",
]
