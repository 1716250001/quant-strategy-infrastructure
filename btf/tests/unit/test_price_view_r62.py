# -*- coding: utf-8 -*-
"""**R62′（N64；19 号 §118）消费侧价格视图**：喂给策略的 bar 走 qfq（记账仍名义价）。

硬门槛断言：
    - **E1 PIT**：**截断 T 之后因子** ⇒ T 日消费侧价格**不变**（⇒ 未用 hfq、无前视）；
    - **E3 记账侧名义价**：① 定价无关策略下 `nominal` vs `adjusted` ⇒
      fills/snapshots/NAV **逐位一致**；② 读价策略在 `adjusted` 下 ⇒ **成交价仍为名义价**；
    - **E7 反例 5 条**：①折算 ②分红 ③分红+折算（同标的） ④因子缺失（**禁静默**）
      ⑤ref 边界（`period.start` 无因子行）。
另：`price_view` 非法值拒收 · `nominal` 无复权注记 · **指数豁免**（`index_daily` 无复权语义）。

口径（§118.2/§118.3）：`qfq(t) = price(t)·f(t)/f(ref)`，`ref = period.start`；
**禁 hfq**（依赖全序列因子 ⇒ 前视污染）；降级（因子缺失）回退 1.0 ＋ 披露。
"""
from __future__ import annotations

import pytest
from btf.data.adjust import TushareAdjustService
from btf.data.memory import MemoryFeed
from btf.domain.market import Bar, TradingState
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.engine.loop import Engine
from btf.execution.cost import ZeroCostModel
from btf.execution.handler import NextOpenHandler
from btf.strategy.base import StrategyBase
from btf.strategy.rebalance import TargetPortfolio

D1 = TradingDate.from_iso(2015, 1, 5)
D2 = TradingDate.from_iso(2015, 1, 6)
D3 = TradingDate.from_iso(2015, 1, 7)
D4 = TradingDate.from_iso(2015, 1, 8)
D5 = TradingDate.from_iso(2015, 1, 9)
DAYS = [D1, D2, D3, D4, D5]
SYM = "510580.SH"          # ETF（fund_adj 路由）；指数用 000300.SH


class _StubAdjust(TushareAdjustService):
    """因子表桩：`{symbol: [(YYYYMMDD, factor), …]}`（**不读主库**）。"""

    def __init__(self, table: dict[str, list[tuple[str, float]]]):
        super().__init__()
        self._table = table

    def _load(self, symbol: str) -> tuple[list[str], list[float]]:
        rows = self._table.get(symbol)
        if not rows:
            self.missing_factor_symbols.add(symbol)
            return ([], [])
        return ([r[0] for r in rows], [r[1] for r in rows])


class _Capture(StrategyBase):
    """捕获策略可见序列（逐日 close/history）——**不交易**。"""

    def __init__(self, symbol: str = SYM, n: int = 3):
        self.symbol = symbol
        self.n = n
        self.closes: dict[str, float] = {}
        self.tails: dict[str, list[float]] = {}

    def on_close(self, ctx, date) -> None:
        bar = ctx.bar(self.symbol)
        if bar is not None:
            self.closes[date.to_ymd()] = bar.close
        tail = ctx.history(self.symbol, date, n_bars=self.n)
        if tail:
            self.tails[date.to_ymd()] = [b.close for b in tail]


class _Scripted(StrategyBase):
    """定价无关的脚本策略（按日期提交目标；**不读价**）。"""

    def __init__(self, script: dict[str, dict[str, float]]):
        self.script = script

    def on_close(self, ctx, date) -> None:
        targets = self.script.get(date.to_ymd())
        if targets is not None:
            ctx.submit_target(TargetPortfolio(rebalance_date=date,
                                              targets=targets))


def _bar(symbol: str, day: TradingDate, px: float, pre: float | None = None) -> Bar:
    return Bar(symbol=symbol, date=day, open=px, high=px, low=px, close=px,
               volume=1_000_000.0, amount=px * 1_000_000.0,
               pre_close=px if pre is None else pre)


def _feed(closes: list[float], *, symbol: str = SYM,
          instrument: Instrument | None = None) -> tuple[MemoryFeed, dict]:
    bars = [_bar(symbol, d, px) for d, px in zip(DAYS, closes, strict=True)]
    # 状态面板（限价 ±10%，非停牌）——无状态行会令撮合拒单，故必须给出
    states: dict[tuple[str, str], TradingState] = {}
    for i, (d, _px) in enumerate(zip(DAYS, closes, strict=True)):
        pre = closes[i - 1] if i else closes[0]
        states[(d.to_ymd(), symbol)] = TradingState(
            symbol=symbol, date=d,
            limit_up_price=round(pre * 1.1, 2),
            limit_down_price=round(pre * 0.9, 2),
            is_suspended=False, is_st=False, is_delisted=False,
            is_limit_up=False, is_limit_down=False)
    inst = {symbol: instrument or Instrument(
        symbol=symbol, asset_class=AssetClass.ETF, board="etf", lot_size=100)}
    feed = MemoryFeed(bars, states=states, instruments=inst)
    return feed, inst


def _engine(feed: MemoryFeed, strategy: StrategyBase, inst: dict, *,
            price_view: str, adjust: TushareAdjustService | None) -> Engine:
    return Engine(
        feed, strategy, start=D1, end=D5, initial_cash=100_000.0,
        handler=NextOpenHandler(cost_model=ZeroCostModel(), instruments=inst),
        instruments=inst, cross_section=sorted(inst),
        price_view=price_view, adjust_service=adjust,
    )


# ───────────────────────── E1：PIT（截断不变量） ─────────────────────────

def test_e1_truncating_future_factors_leaves_tday_price_unchanged():
    """**E1（本单核心）**：把 T 之后的因子行删掉 ⇒ T 日消费侧价格**逐位不变**。

    ⇒ 证明 `qfq` 只依赖 ≤T 的因子（**未用 hfq**，无前视污染）。
    """
    closes = [10.0, 10.0, 10.0, 10.0, 10.0]
    full = _StubAdjust({SYM: [("20150102", 1.0), ("20150107", 2.0),
                              ("20150109", 4.0)]})          # D5 有跳变（「未来」）
    trunc = _StubAdjust({SYM: [("20150102", 1.0), ("20150107", 2.0)]})

    feed1, inst1 = _feed(closes)
    s1 = _Capture()
    _engine(feed1, s1, inst1, price_view="adjusted", adjust=full).run()
    feed2, inst2 = _feed(closes)
    s2 = _Capture()
    _engine(feed2, s2, inst2, price_view="adjusted", adjust=trunc).run()

    for day in ("20150105", "20150106", "20150107"):        # T ≤ D3 全部相同
        assert s1.closes[day] == s2.closes[day]
        assert s1.tails[day] == s2.tails[day]
    # 反向锚：被截断的因子只在**其生效日**（D5）造成差异——不是整体失效
    assert s1.closes["20150108"] == s2.closes["20150108"]    # D5 行不影响 D4
    assert s1.closes["20150109"] == pytest.approx(40.0)      # 全表：×4
    assert s2.closes["20150109"] == pytest.approx(20.0)      # 截断：×2


def test_qfq_not_hfq_direction():
    """口径方向锚：`ref` 基准 ⇒ 起点日 adjusted == nominal（hfq 会让起点放大）。"""
    feed, inst = _feed([10.0, 10.0, 10.0, 10.0, 10.0])
    cap = _Capture()
    _engine(feed, cap, inst, price_view="adjusted",
            adjust=_StubAdjust({SYM: [("20141231", 1.0), ("20150107", 2.0)]})).run()
    assert cap.closes["20150105"] == pytest.approx(10.0)     # 起点：f(t)=f(ref) ⇒ 比值 1
    assert cap.closes["20150107"] == pytest.approx(20.0)     # 跳变后：×2（连续序列）


# ───────────────────────── E3：记账侧名义价 ─────────────────────────

def test_e3_accounting_bitwise_identical_between_views():
    """**E3①**：定价无关策略下 `nominal` 与 `adjusted` 的记账产物**逐位一致**。

    ⇒ 价格视图**不影响**撮合/成本/费用/NAV/snapshots（ADR-6 未被破坏）。
    """
    script = {"20150105": {SYM: 0.5}, "20150107": {}}
    adj = _StubAdjust({SYM: [("20150102", 1.0), ("20150107", 2.0)]})

    feed_a, inst_a = _feed([10.0, 10.0, 20.0, 20.0, 20.0])
    ra = _engine(feed_a, _Scripted(script), inst_a,
                 price_view="nominal", adjust=None).run()
    feed_b, inst_b = _feed([10.0, 10.0, 20.0, 20.0, 20.0])
    rb = _engine(feed_b, _Scripted(script), inst_b,
                 price_view="adjusted", adjust=adj).run()

    assert ra.price_view == "nominal" and rb.price_view == "adjusted"
    assert rb.price_view_ref == D1
    assert ra.fills == rb.fills                       # Fill 全字段（含 price/fee/ref_price）
    assert ra.rejections == rb.rejections
    assert [(s.date, s.cash, s.total_value, s.market_value) for s in ra.snapshots] == \
           [(s.date, s.cash, s.total_value, s.market_value) for s in rb.snapshots]


def test_e3_adjusted_view_keeps_nominal_fill_price():
    """**E3②**：`adjusted` 下策略看到复权价，但**成交价仍是名义价**（ADR-6）。

    构造：D2 开盘名义 10，**因子 D2 起 ×2** ⇒ 策略视图中该 bar 为 20；
    若撮合误用消费视图，成交价会是 20 ⇒ 本断言即 ADR-6 捕获网。
    """
    adj = _StubAdjust({SYM: [("20150102", 1.0), ("20150106", 2.0)]})
    feed, inst = _feed([10.0, 10.0, 10.0, 10.0, 10.0])

    class _CapTrade(_Capture):
        """既捕获视图又下单（同一策略内对照两条口径）。"""

        def on_close(self, ctx, date) -> None:
            super().on_close(ctx, date)
            if date == D1:
                ctx.submit_target(TargetPortfolio(rebalance_date=date,
                                                  targets={SYM: 0.5}))

    cap = _CapTrade()
    res = _engine(feed, cap, inst, price_view="adjusted", adjust=adj).run()
    assert cap.closes["20150106"] == pytest.approx(20.0)   # 策略视图=复权价（×2）
    assert res.fills, "应有一笔买入"
    fill = res.fills[0]
    assert fill.fill_date == D2
    assert fill.price == pytest.approx(10.0)          # 名义价（非 20.0 复权价）
    assert fill.ref_price == pytest.approx(10.0)      # 滑点前基准同名义价
    assert res.snapshots[-1].total_value == pytest.approx(
        res.snapshots[0].total_value)                 # 价格不变 ⇒ NAV 不变


# ───────────────────────── E7：反例五条 ─────────────────────────

@pytest.mark.parametrize("label,factors,nominal,min_jump", [
    ("折算（factor ↓，价 ↑）", [("20150102", 1.0), ("20150107", 0.25)],
     [10.0, 10.0, 40.0, 40.0, 40.0], 0.5),
    ("分红（factor ↑，价 ↓）", [("20150102", 1.0), ("20150107", 1.02)],
     [10.0, 10.0, 10.0 / 1.02, 10.0 / 1.02, 10.0 / 1.02], 0.015),
    ("分红+折算（同标的；折算 ×4 价 ＋ 分红 −2%）",
     [("20150102", 1.0), ("20150107", 0.25 / 0.98)],
     [10.0, 10.0, 40.0 * 0.98, 40.0 * 0.98, 40.0 * 0.98], 0.5),
])
def test_e7_corporate_action_continuity(label, factors, nominal, min_jump):
    """**E7①②③**：折算 / 分红 / 两者叠加 ⇒ 消费侧序列在事件点**不再断裂**。

    构造：事件日名义价有跳变（前提断言按事件类型取阈值），因子按
    `f = 名义跳变比` 反向设置 ⇒ 复权序列应为**常数**（连续）。
    """
    feed, inst = _feed(nominal)
    cap = _Capture()
    _engine(feed, cap, inst, price_view="adjusted",
            adjust=_StubAdjust({SYM: factors})).run()
    adj = [cap.closes[d] for d in ("20150105", "20150106", "20150107",
                                   "20150108", "20150109")]
    assert abs(nominal[2] / nominal[1] - 1.0) > min_jump, f"{label}：名义跳变前提不足"
    for prev, cur in ((1, 2), (2, 3), (3, 4)):
        assert adj[cur] == pytest.approx(adj[prev], rel=1e-6), label


def test_e7_missing_factor_falls_back_and_discloses():
    """**E7④**：因子缺失 ⇒ 回退 1.0（序列=名义）＋ **披露**（禁静默）。"""
    feed, inst = _feed([10.0, 10.0, 40.0, 40.0, 40.0])
    cap = _Capture()
    stub = _StubAdjust({})                       # 表内全无该标的
    res = _engine(feed, cap, inst, price_view="adjusted", adjust=stub).run()
    assert cap.closes["20150107"] == pytest.approx(40.0)     # 1.0 回退 ⇒ 名义价
    assert SYM in stub.missing_factor_symbols
    assert res.adjust_notes, "缺失必须披露（R62′/§118.3-4）"
    assert "复权因子缺失" in res.adjust_notes[0]


def test_e7_ref_boundary_before_first_factor_row():
    """**E7⑤**：`period.start` 早于因子表首行 ⇒ 相对锚（不崩、首日=名义）。"""
    feed, inst = _feed([10.0, 10.0, 10.0, 10.0, 10.0])
    cap = _Capture()
    # 首行在 D3（晚于 period.start=D1）；ref 落空 ⇒ 取表内最早因子作基准
    _engine(feed, cap, inst, price_view="adjusted",
            adjust=_StubAdjust({SYM: [("20150107", 3.0)]})).run()
    assert cap.closes["20150105"] == pytest.approx(10.0)     # f(ref)=f(t)=3.0 ⇒ 比值 1
    assert cap.closes["20150109"] == pytest.approx(10.0)


# ───────────────────────── 边界与豁免 ─────────────────────────

def test_nominal_view_has_no_adjust_notes():
    """`nominal`（旧行为/对照档）：不看因子、无复权注记。"""
    feed, inst = _feed([10.0, 10.0, 20.0, 20.0, 20.0])
    cap = _Capture()
    res = _engine(feed, cap, inst, price_view="nominal",
                  adjust=_StubAdjust({SYM: [("20150102", 1.0), ("20150107", 2.0)]})).run()
    assert cap.closes["20150107"] == pytest.approx(20.0)     # 名义价原样
    assert res.adjust_notes == ()
    assert res.price_view_ref is None


def test_index_exempt_from_adjustment():
    """指数（`index_daily`）**无复权语义** ⇒ 即便因子表有行也原样返回。"""
    feed, inst = _feed([1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
                       symbol="000300.SH",
                       instrument=Instrument(symbol="000300.SH",
                                             asset_class=AssetClass.STOCK,
                                             board="index"))
    cap = _Capture(symbol="000300.SH")
    _engine(feed, cap, inst, price_view="adjusted",
            adjust=_StubAdjust({"000300.SH": [("20150102", 1.0),
                                              ("20150107", 2.0)]})).run()
    assert cap.closes["20150107"] == pytest.approx(1000.0)   # 未被 ×2


def test_invalid_price_view_rejected():
    feed, inst = _feed([10.0] * 5)
    with pytest.raises(ValueError, match="price_view"):
        _engine(feed, _Capture(), inst, price_view="hfq", adjust=None)
