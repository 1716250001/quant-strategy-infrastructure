# -*- coding: utf-8 -*-
"""L1 单测：MemoryFeed + SimClock + 三手写场景（PoC-2 任务 2.2 验收）。

验收锚点：3 个手写场景数据集（T+1 冻结/涨跌停/停牌各一）；
截面迭代日期升序、停牌股当日缺席 map（协议语义）。
"""
from __future__ import annotations

import pytest
from btf.engine.clock import SimClock

from tests.fixtures.scenarios import (
    ALL_DATES,
    D1,
    D3,
    combined_scenario,
    limit_scenario,
    suspend_scenario,
    t_plus1_scenario,
)

pytestmark = [pytest.mark.l1]


class TestSimClock:
    def test_advance_ascending(self):
        c = SimClock()
        assert c.current is None
        c.advance(D1)
        assert c.current == D1
        c.advance(ALL_DATES[1])
        assert c.n_days == 2

    def test_advance_violation_raises(self):
        c = SimClock()
        c.advance(ALL_DATES[1])
        with pytest.raises(RuntimeError, match="时序违例"):
            c.advance(D1)          # 回跳
        with pytest.raises(RuntimeError, match="时序违例"):
            c.advance(ALL_DATES[1])  # 重复

    def test_of_validates_order(self):
        with pytest.raises(ValueError, match="非升序"):
            SimClock.of([ALL_DATES[1], D1])
        assert SimClock.of(ALL_DATES) == ALL_DATES


class TestMemoryFeed:
    def test_bars_iteration_ascending_and_suspended_absent(self):
        """停牌语义：截面日期升序；停牌日该标的缺席 map（与主库口径一致）。"""
        sc = suspend_scenario()
        dates = []
        for d, section in sc.feed.bars(None, D1, ALL_DATES[-1]):
            dates.append(d)
            if d == D3:
                assert "000002.SZ" not in section   # 停牌日缺席
            else:
                assert "000002.SZ" in section
        assert dates == ALL_DATES

    def test_bars_symbol_filter(self):
        sc = t_plus1_scenario()
        seen = set()
        for _, section in sc.feed.bars(["000001.SZ"], D1, ALL_DATES[-1]):
            assert set(section) <= {"000001.SZ"}
            seen |= set(section)
        assert seen == {"000001.SZ"}

    def test_bars_of_range(self):
        sc = t_plus1_scenario()
        bars = sc.feed.bars_of("000001.SZ", ALL_DATES[1], ALL_DATES[3])
        assert [b.date for b in bars] == ALL_DATES[1:4]

    def test_trading_states_subset(self):
        sc = t_plus1_scenario()
        all_states = sc.feed.trading_states(D1)
        assert set(all_states) == {"000001.SZ", "511380.SH"}
        only = sc.feed.trading_states(D1, ["511380.SH"])
        assert set(only) == {"511380.SH"}

    def test_corporate_actions_default_empty(self):
        sc = combined_scenario()
        assert sc.feed.corporate_actions(D1, ALL_DATES[-1]) == []


class TestScenarios:
    """三场景数据完整性（2.2 验收：T+1 冻结/涨跌停/停牌各一）。"""

    def test_t_plus1_scenario(self):
        sc = t_plus1_scenario()
        assert set(sc.instruments) == {"000001.SZ", "511380.SH"}
        # ETF 为货币子类（T+0 语义来源，04 §8.2.2 E2）
        assert sc.instruments["511380.SH"].etf_subclass is not None
        assert len(sc.feed.bars_of("000001.SZ", D1, ALL_DATES[-1])) == 5

    def test_limit_scenario(self):
        sc = limit_scenario()
        bars = {b.date.to_ymd(): b for b in sc.feed.bars_of("600000.SH", D1, ALL_DATES[-1])}
        assert bars["20150106"].open == 11.0    # D2 涨停开盘（买入拒锚点）
        assert bars["20150107"].open == 9.9     # D3 跌停开盘（卖出拒锚点）
        st = sc.feed.trading_states(ALL_DATES[1])
        assert st["600000.SH"].limit_up_price == 11.0
        assert st["600000.SH"].is_limit_up is True

    def test_suspend_scenario(self):
        sc = suspend_scenario()
        st = sc.feed.trading_states(D3)
        assert st["000002.SZ"].is_suspended is True
        assert st["000002.SZ"].tradable is False
        assert len(sc.feed.bars_of("000002.SZ", D1, ALL_DATES[-1])) == 4  # D3 缺席

    def test_combined_covers_all(self):
        sc = combined_scenario()
        assert len(sc.instruments) == 4
        assert len(sc.feed.dates) == 5
