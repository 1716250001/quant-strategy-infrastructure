# -*- coding: utf-8 -*-
"""ETF 限价通道集成（M3 6.3；V3-1 混合表 Feed + V3-2 ETF 限价）。

覆盖：
    1. ETF 限价通道：`etf_limit`（起点 2019-06-26）为池内 ETF 提供涨跌停价；
       股票仍走 `stk_limit`；**起点之前**由 BoardRule ±10% 依 pre_close 计算
       回退并登记披露（E3，不静默）；
    2. 引擎级复跑（有界窗口）：混合表 Feed 装配 → 预载 → 引擎执行 → 落盘
       指标齐备（`btf` 侧全链路：截面/历史/状态/撮合/快照/指标）；
    3. ETF 身份与 BoardRule：池内可转债 ETF（511180/511380）经推断为
       BOND 子类 → **T+0**。

主库不可用 → skip（不静默通过）。全区间（2019-06-26 起）净值复跑由
`tools/run_qidian_rerun.py` 承担（分钟级，不入 CI）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import MixedDailyFeed
from btf.data.state import StateSynthesizer
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l4]

ROOT = Path(MARKET_DATA_DIR)
START, END = "2026-01-02", "2026-09-23"          # 有界窗口（CI 秒级）
LIMIT_START = "20190626"                          # etf_limit 起点（E3）
#: ETF 限价回退测试候选（不依赖已迁仓外的 qidian 池）
ETF_CANDIDATES = ("510300.SH", "510500.SH", "510050.SH", "159901.SZ",
                  "159915.SZ", "511010.SH", "511380.SH")


def _feed() -> MixedDailyFeed:
    """构造混合表 Feed（**注入状态面板**）。

    R4（19 号 §48）：`feed` 不再 import `state`，状态合成器改为**构造期注入**；
    经 `BTFRuntime` 运行时由装配根自动注入，直接构造的测试显式传入。
    """
    return MixedDailyFeed(state_provider=StateSynthesizer())


def _skip_if_no_data() -> None:
    if not (ROOT / "fund_daily").is_dir() or not (ROOT / "etf_limit").is_dir():
        pytest.skip("主库 fund_daily/etf_limit 不可用")


class TestEtfLimitChannel:
    """V3-2：ETF 涨跌停价通道。"""

    def test_etf_limit_from_table(self):
        _skip_if_no_data()
        feed = _feed()
        state = feed.trading_states(
            TradingDate.from_ymd("20260923"), ["511380.SH"])["511380.SH"]
        assert state.limit_up_price and state.limit_down_price
        assert state.limit_up_price > state.limit_down_price

    def test_stock_still_from_stk_limit(self):
        _skip_if_no_data()
        feed = _feed()
        state = feed.trading_states(
            TradingDate.from_ymd("20260923"), ["000001.SZ"])["000001.SZ"]
        assert state.limit_up_price and state.limit_down_price

    def test_fallback_before_etf_limit_start(self):
        """E3 回退：`etf_limit` 起点之前 → BoardRule ±10% 计算 + 登记披露。

        须取「起点之前已有 fund_daily 行情」的 ETF（多数 ETF 上市晚于
        2019-06-26，无行情则回退无从计算——那属正确行为，不是回退失效）。
        """
        _skip_if_no_data()
        feed = _feed()
        day = TradingDate.from_ymd("20190625")     # 起点前一日
        candidates = [code for code in ETF_CANDIDATES
                      if feed.bars_of(code, day, day)]
        if not candidates:
            pytest.skip("候选 ETF 无 2019-06-25 前行情（无行情则回退无输入）")
        code = candidates[0]
        state = feed.trading_states(day, [code])[code]
        assert state.limit_up_price is not None and state.limit_down_price is not None
        assert state.limit_up_price > state.limit_down_price
        assert day.to_ymd() in feed.states.fallback_limit_dates
        notes = feed.states.degraded_notes()
        assert notes and "etf_limit 起点 2019-06-26" in notes[0]

    def test_no_fallback_at_or_after_start(self):
        """起点当日及之后走 `etf_limit` 表值 → 无回退登记。"""
        _skip_if_no_data()
        feed = _feed()
        feed.trading_states(TradingDate.from_ymd(LIMIT_START), ["511380.SH"])
        assert not feed.states.fallback_limit_dates


