# -*- coding: utf-8 -*-
"""手写场景数据集（18 号计划 2.2 验收：三个场景——T+1 冻结 / 涨跌停 / 停牌）。

设计纪律：
    - 价格取整值避开交易所涨停价舍入边界（round 半位），使拒单判定只考验
      引擎语义而非浮点巧合；
    - 停牌口径与主库一致：停牌日**无 bar**（截面缺席）+ 状态面板 is_suspended；
    - 每场景返回 (MemoryFeed, instruments)；instrument 供 ExecutionHandler
      查 lot_size / t_plus（04 §8.2.2 BoardRule）。
"""
from __future__ import annotations

from typing import NamedTuple

from btf.data.memory import MemoryFeed
from btf.domain.market import Bar, TradingState
from btf.domain.types import (
    AssetClass,
    EtfSubclass,
    Instrument,
    TradingDate,
)

# 场景交易日：2015-01-05(一) .. 01-09(五)
D1 = TradingDate.from_iso(2015, 1, 5)
D2 = TradingDate.from_iso(2015, 1, 6)
D3 = TradingDate.from_iso(2015, 1, 7)
D4 = TradingDate.from_iso(2015, 1, 8)
D5 = TradingDate.from_iso(2015, 1, 9)
ALL_DATES = [D1, D2, D3, D4, D5]


class Scenario(NamedTuple):
    feed: MemoryFeed
    instruments: dict[str, Instrument]


def make_bar(symbol: str, day: TradingDate, o: float, h: float, lo: float,
             c: float, pre: float, vol: float = 1_000_000.0) -> Bar:
    return Bar(symbol=symbol, date=day, open=o, high=h, low=lo, close=c,
               volume=vol, amount=c * vol, pre_close=pre)


def make_state(symbol: str, day: TradingDate, *, pre: float,
               limit_pct: float = 0.10, suspended: bool = False,
               st: bool = False, delisted: bool = False,
               limit_up: bool = False, limit_down: bool = False) -> TradingState:
    """按主板口径合成状态行（涨停价 = pre×(1+pct)，取整值场景下精确）。"""
    return TradingState(
        symbol=symbol, date=day,
        limit_up_price=round(pre * (1 + limit_pct), 2),
        limit_down_price=round(pre * (1 - limit_pct), 2),
        is_suspended=suspended, is_st=st, is_delisted=delisted,
        is_limit_up=limit_up, is_limit_down=limit_down,
    )


def _stock(symbol: str, board: str = "main") -> Instrument:
    return Instrument(symbol=symbol, asset_class=AssetClass.STOCK, board=board,
                      lot_size=100)


def _etf(symbol: str, sub: EtfSubclass) -> Instrument:
    return Instrument(symbol=symbol, asset_class=AssetClass.ETF, board="etf",
                      etf_subclass=sub, lot_size=100)


# ─────────────────────────────────────────────
# 场景一：T+1 冻结（000001.SZ 股票 T+1 × 511380.SH 货币 ETF T+0）
# ─────────────────────────────────────────────
def t_plus1_scenario() -> Scenario:
    bars: list[Bar] = []
    states: dict[tuple[str, str], TradingState] = {}
    stock_px = [10.0, 10.1, 10.2, 10.1, 10.3]
    etf_px = [100.0, 100.0, 100.1, 100.0, 100.2]
    for i, d in enumerate(ALL_DATES):
        pre_s = stock_px[i - 1] if i else 10.0
        bars.append(make_bar("000001.SZ", d, stock_px[i], stock_px[i],
                             stock_px[i], stock_px[i], pre_s))
        states[(d.to_ymd(), "000001.SZ")] = make_state(
            "000001.SZ", d, pre=pre_s if i else 10.0 - 0.0,
            suspended=False)
        pre_e = etf_px[i - 1] if i else 100.0
        bars.append(make_bar("511380.SH", d, etf_px[i], etf_px[i],
                             etf_px[i], etf_px[i], pre_e))
        states[(d.to_ymd(), "511380.SH")] = make_state("511380.SH", d, pre=pre_e)
    instruments = {
        "000001.SZ": _stock("000001.SZ"),
        "511380.SH": _etf("511380.SH", EtfSubclass.MONEY),  # 货币 ETF：T+0（E2）
    }
    return Scenario(
        MemoryFeed(bars, states, instruments, dates=ALL_DATES), instruments)


# ─────────────────────────────────────────────
# 场景二：涨跌停（600000.SH 主板 ±10%，取整价格精确边界）
#   D2 开盘=涨停价 11.0（买入拒）；D3 开盘=跌停价 9.9（卖出拒）
# ─────────────────────────────────────────────
def limit_scenario() -> Scenario:
    bars = [
        make_bar("600000.SH", D1, o=10.0, h=10.2, lo=9.9, c=10.0, pre=10.0),
        make_bar("600000.SH", D2, o=11.0, h=11.0, lo=10.8, c=11.0, pre=10.0),   # 涨停
        make_bar("600000.SH", D3, o=9.9, h=10.0, lo=9.9, c=9.9, pre=11.0),      # 跌停
        make_bar("600000.SH", D4, o=10.2, h=10.4, lo=10.1, c=10.3, pre=9.9),
        make_bar("600000.SH", D5, o=10.5, h=10.6, lo=10.4, c=10.5, pre=10.3),
    ]
    states = {
        (D1.to_ymd(), "600000.SH"): make_state("600000.SH", D1, pre=10.0),
        (D2.to_ymd(), "600000.SH"): make_state("600000.SH", D2, pre=10.0, limit_up=True),
        (D3.to_ymd(), "600000.SH"): make_state("600000.SH", D3, pre=11.0, limit_down=True),
        (D4.to_ymd(), "600000.SH"): make_state("600000.SH", D4, pre=9.9),
        (D5.to_ymd(), "600000.SH"): make_state("600000.SH", D5, pre=10.3),
    }
    instruments = {"600000.SH": _stock("600000.SH")}
    return Scenario(
        MemoryFeed(bars, states, instruments, dates=ALL_DATES), instruments)


# ─────────────────────────────────────────────
# 场景三：停牌（000002.SZ D3 停牌：无 bar + is_suspended；D4 复牌）
# ─────────────────────────────────────────────
def suspend_scenario() -> Scenario:
    bars = [
        make_bar("000002.SZ", D1, o=8.0, h=8.1, lo=7.9, c=8.0, pre=8.0),
        make_bar("000002.SZ", D2, o=8.1, h=8.2, lo=8.0, c=8.1, pre=8.0),
        # D3 停牌：无 bar（口径=当日截面缺席，与主库 daily 一致）
        make_bar("000002.SZ", D4, o=8.2, h=8.3, lo=8.1, c=8.2, pre=8.1),
        make_bar("000002.SZ", D5, o=8.2, h=8.4, lo=8.2, c=8.3, pre=8.2),
    ]
    states = {
        (D1.to_ymd(), "000002.SZ"): make_state("000002.SZ", D1, pre=8.0),
        (D2.to_ymd(), "000002.SZ"): make_state("000002.SZ", D2, pre=8.0),
        (D3.to_ymd(), "000002.SZ"): make_state("000002.SZ", D3, pre=8.1, suspended=True),
        (D4.to_ymd(), "000002.SZ"): make_state("000002.SZ", D4, pre=8.1),
        (D5.to_ymd(), "000002.SZ"): make_state("000002.SZ", D5, pre=8.2),
    }
    instruments = {"000002.SZ": _stock("000002.SZ")}
    return Scenario(
        MemoryFeed(bars, states, instruments, dates=ALL_DATES), instruments)


# ─────────────────────────────────────────────
# 组合场景：三场景并集（引擎循环/确定性双跑用——覆盖全部拒单语义）
# ─────────────────────────────────────────────
def combined_scenario() -> Scenario:
    a = t_plus1_scenario()
    b = limit_scenario()
    c = suspend_scenario()
    bars = [*a.feed.bars_all, *b.feed.bars_all, *c.feed.bars_all]
    states = {**a.feed.states_all, **b.feed.states_all, **c.feed.states_all}
    instruments = {**a.instruments, **b.instruments, **c.instruments}
    return Scenario(
        MemoryFeed(bars, states, instruments, dates=ALL_DATES), instruments)


__all__ = [
    "ALL_DATES",
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "Scenario",
    "combined_scenario",
    "limit_scenario",
    "make_bar",
    "make_state",
    "suspend_scenario",
    "t_plus1_scenario",
]
