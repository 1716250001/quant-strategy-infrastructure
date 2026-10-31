# -*- coding: utf-8 -*-
"""L2：引擎级 `ctx.history` 缓存的生命周期与护栏（19 号 §194 T-6 / T-7）。

- **T-6**：同一 `Engine` 连续 `run()` 两次 ⇒ 缓存对象**重建**（不读走上轮），
  且两次结果逐项相同（Z-2 脏读守卫）。
- **T-7**：容量触顶 ⇒ `evictions > 0`，且 80% 告警**只打一次**。
"""
from __future__ import annotations

import logging

import pytest
from btf.data.memory import MemoryFeed
from btf.domain.market import TradingState
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.engine.loop import Engine
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext

from tests.fixtures.scenarios import make_bar

pytestmark = [pytest.mark.l2]

D1 = TradingDate.from_iso(2015, 1, 5)
D2 = TradingDate.from_iso(2015, 1, 6)
D3 = TradingDate.from_iso(2015, 1, 7)


def _inst(sym: str) -> Instrument:
    return Instrument(symbol=sym, asset_class=AssetClass.STOCK, board="main",
                      lot_size=100)


class HistoryProbe(StrategyBase):
    """每日收盘对给定标的全查一次 `history`（触发缓存装载/命中）。"""

    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.seen: list[list[str]] = []

    def on_open(self, ctx: StrategyContext, date: TradingDate) -> None:
        pass

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        self.seen.append([ctx.history(s, date)[0].date.to_ymd()
                          for s in self.symbols])


def _feed(symbols: list[str]) -> MemoryFeed:
    bars, states = [], {}
    for i, sym in enumerate(symbols):
        for d in (D1, D2, D3):
            px = 10.0 + i + (d.to_ymd() != D1.to_ymd())
            bars.append(make_bar(sym, d, px, px, px, px, px - 0.1))
            states[(d.to_ymd(), sym)] = TradingState(
                symbol=sym, date=d, limit_up_price=round(px * 1.1, 2),
                limit_down_price=round(px * 0.9, 2), is_suspended=False,
                is_st=False, is_delisted=False, is_limit_up=False,
                is_limit_down=False)
    return MemoryFeed(bars, states,
                      instruments={s: _inst(s) for s in symbols})


def _engine(symbols: list[str]) -> tuple[Engine, HistoryProbe]:
    probe = HistoryProbe(symbols)
    eng = Engine(_feed(symbols), probe, start=D1, end=D3,
                 instruments={s: _inst(s) for s in symbols},
                 cross_section=symbols)
    return eng, probe


def test_t6_same_engine_two_runs_rebuilds_cache():
    """T-6：连续两次 run ⇒ 缓存重建 + 结果一致（无跨 run 脏读）。"""
    eng, probe = _engine(["000001.SZ"])
    r1 = eng.run()
    cache1 = eng._ctx_history_cache
    seen1 = list(probe.seen)
    r2 = eng.run()
    cache2 = eng._ctx_history_cache
    assert cache2 is not cache1, "缓存未重建（跨 run 脏读风险）"
    assert len(cache1) > 0 and cache2.misses > 0, "第二轮未重新装载"
    assert [s.to_dict() if hasattr(s, "to_dict") else s for s in r1.snapshots] \
        == [s.to_dict() if hasattr(s, "to_dict") else s for s in r2.snapshots]
    assert len(r1.orders) == len(r2.orders)
    assert probe.seen[len(seen1):] == seen1


def test_t7_cache_capacity_warning_once(monkeypatch, caplog):
    """T-7：容量 2、3 标的 ⇒ evictions>0；80% 告警只打一次。"""
    monkeypatch.setenv("CTX_CACHE_MAX", "2")
    caplog.set_level(logging.WARNING)
    eng, _ = _engine(["000001.SZ", "600000.SH", "510300.SH"])
    eng.run()
    assert eng._ctx_history_cache.evictions > 0
    msgs = [r.message for r in caplog.records if "缓存达" in r.message]
    assert len(msgs) == 1, f"告警打了 {len(msgs)} 次（期望 1）"
