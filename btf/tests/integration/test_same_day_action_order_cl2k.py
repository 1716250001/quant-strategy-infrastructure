# -*- coding: utf-8 -*-
"""**R49′（N53，19 号 §99.5）**：同日「折算＋分红」顺序 —— 链路级端到端。

链路（N53 指出的隐式契约）：数据层 `action_order_key`（先折算后分红）
⇒ `feed.corporate_actions` **显式第三键**（不再依赖稳定排序）
⇒ `loop.py` 按序应用 ⇒ `dividend_events.qty_at_ex` ＝ **折算后**份额。

两个用例：
  A **feed 排序**：输入乱序（[分红, 折算]）⇒ 输出 [折算, 分红]（白盒打桩
    `to_etf_corporate_actions`，专测 `feed.py` 自己的排序键）；
  B **引擎端到端**：MemoryFeed ＋ 同日双事件 ⇒ `dividend_events.qty_at_ex`
    ＝折算后份额；**反向对照**（输入 [分红, 折算]）⇒ 得折算前份额
    （证明用例可判别，非恒真）。
"""
from __future__ import annotations

import pathlib

import pytest
from btf.data.memory import MemoryFeed
from btf.domain.action import CorporateAction
from btf.domain.market import Bar, TradingState
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.engine.loop import Engine
from btf.execution.cost import ZeroCostModel
from btf.execution.handler import NextOpenHandler
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

pytestmark = [pytest.mark.l2]

SYM = "510310.SH"                      # 场内 ETF（is_fund_code）
CASH = 100_000.0
INSTRUMENTS = {SYM: Instrument(symbol=SYM, asset_class=AssetClass.ETF,
                               board="main", lot_size=100)}
D1 = TradingDate.from_iso(2024, 9, 19)
D2 = TradingDate.from_iso(2024, 9, 20)
D3 = TradingDate.from_iso(2024, 9, 23)          # 除息/折算同日
D4 = TradingDate.from_iso(2024, 9, 24)
DATES = [D1, D2, D3, D4]
EX = D3


def _cons() -> CorporateAction:
    return CorporateAction(symbol=SYM, ex_date=EX, pay_date=None,
                           record_date=None, cash_div_per_share=0.0,
                           stk_div_per_share=0.0, ann_date=None,
                           consolidation_ratio=0.5,
                           consolidation_source="fund_conv")


def _div() -> CorporateAction:
    return CorporateAction(symbol=SYM, ex_date=EX, pay_date=EX,
                           record_date=None, cash_div_per_share=0.10,
                           stk_div_per_share=0.0, ann_date=None)


class _BuyHold(StrategyBase):
    def __init__(self) -> None:
        self._done = False

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        if not self._done and ctx.bar(SYM) is not None:
            ctx.submit_target(TargetPortfolio(rebalance_date=date,
                                              targets={SYM: 1.0}))
            self._done = True


def _bars() -> list[Bar]:
    out = []
    for d in DATES:
        out.append(Bar(symbol=SYM, date=d, open=1.0, high=1.0, low=1.0,
                       close=1.0, volume=10_000_000.0,
                       amount=10_000_000.0, pre_close=1.0))
    return out


def _feed(actions: list[CorporateAction]) -> MemoryFeed:
    states = {(d.to_ymd(), SYM): TradingState(
        symbol=SYM, date=d, limit_up_price=1.1, limit_down_price=0.9,
        is_suspended=False, is_st=False, is_delisted=False,
        is_limit_up=False, is_limit_down=False) for d in DATES}
    return MemoryFeed(_bars(), states, INSTRUMENTS, dates=DATES,
                      actions=actions)


def _run(actions: list[CorporateAction], tmp_path: pathlib.Path):
    return Engine(
        _feed(actions), _BuyHold(), start=D1, end=D4, initial_cash=CASH,
        handler=NextOpenHandler(cost_model=ZeroCostModel(),
                                instruments=INSTRUMENTS),
        instruments=INSTRUMENTS,
        event_log_path=tmp_path / "events.jsonl",
    ).run()


# ── A：feed 排序显式第三键（白盒打桩，专测 feed.py 自身排序） ──
def _empty_dividend_table(root: pathlib.Path) -> None:
    """股票侧 `dividend` 表（空）——feed 的股票路径会读它（避免 ReaderError）。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    (root / "dividend").mkdir(parents=True, exist_ok=True)
    names = ["ts_code", "ex_date", "pay_date", "record_date", "ann_date",
             "div_proc", "stk_bo_rate", "stk_co_rate", "cash_div", "end_date"]
    cols = {n: pa.array([], type=pa.string()) for n in names
            if n not in ("stk_bo_rate", "stk_co_rate", "cash_div")}
    for n in ("stk_bo_rate", "stk_co_rate", "cash_div"):
        cols[n] = pa.array([], type=pa.float64())
    pq.write_table(pa.table(cols), root / "dividend" / "2024.parquet")


def test_feed_sort_explicit_third_key(tmp_path, monkeypatch):
    from btf.data import etf_corp_actions as eca
    from btf.data.feed import MixedDailyFeed

    div, cons = _div(), _cons()
    _empty_dividend_table(tmp_path)
    monkeypatch.setattr(
        eca, "to_etf_corporate_actions",
        lambda root, syms, s, e: ([div, cons], [], {"n_symbols": 1,
                                                    "n_div": 1, "n_conv": 1,
                                                    "n_unmodeled": 0}))
    feed = MixedDailyFeed(root=tmp_path)
    acts = feed.corporate_actions(EX, EX, symbols=[SYM])
    mine = [a for a in acts if a.symbol == SYM]
    assert len(mine) == 2
    assert mine[0].consolidation_ratio != 1.0      # 折算在前（显式键，非输入序）
    assert mine[1].cash_div_per_share == 0.1       # 分红在后


# ── B：引擎端到端（正向 + 反向对照） ──
def test_engine_qty_at_ex_is_post_consolidation(tmp_path):
    res = _run([_cons(), _div()], tmp_path)        # 数据层定义的顺序
    cons_ev = next(e for e in res.consolidations if e["symbol"] == SYM)
    div_ev = next(e for e in res.dividend_events if e["symbol"] == SYM)
    assert cons_ev["ex_date"] == EX.to_ymd()
    # 买入 100_000 份（CASH=100_000 / 价 1.0）⇒ 折算 0.5 ⇒ 50_000 份
    assert cons_ev["qty_before"] == 100_000
    assert cons_ev["qty_after"] == 50_000
    # **核心断言（N53）**：同日分红按**折算后**份额计息
    assert div_ev["qty_at_ex"] == cons_ev["qty_after"] == 50_000
    assert div_ev["amount"] == pytest.approx(50_000 * 0.10)


def test_engine_reversed_order_gives_pre_consolidation_qty(tmp_path):
    """反向对照：输入 [分红, 折算] ⇒ `qty_at_ex`＝折算前份额（用例可判别）。"""
    res = _run([_div(), _cons()], tmp_path)
    cons_ev = next(e for e in res.consolidations if e["symbol"] == SYM)
    div_ev = next(e for e in res.dividend_events if e["symbol"] == SYM)
    assert cons_ev["qty_after"] == 50_000
    assert div_ev["qty_at_ex"] == 100_000          # 折算前（错误顺序的后果）
    assert div_ev["qty_at_ex"] != cons_ev["qty_after"]
