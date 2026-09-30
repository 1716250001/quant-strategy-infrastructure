# -*- coding: utf-8 -*-
"""奇点战法信号层单测（M3 任务 6.3）。

覆盖：
    1. W-FRI 周线自聚合（周末日映射 / OHLCV 归并 / 跨周切分）；
    2. 标准 KDJ（手算基准 / 样本不足 / 平窗 RSV=50）；
    3. CCI（手算基准 / 样本不足 / 零偏差降级）；
    4. 信号阈值与**买入优先**、双重确认同向判定；
    5. 真源加载：标的池/阈值/参数**运行时读取**（btf 源码零清单副本）；
    6. 分组机检：真源注释分组 = 14 推荐 / 23 双回测 / 4 行业 / 2 可转债（B2）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest
from btf.config.paths import QIDIAN_REF
from btf.strategy.qidian import (
    BUY,
    SELL,
    IndicatorParams,
    calc_cci,
    calc_kdj,
    dual_signal,
    load_reference,
    params_of,
    pool_groups,
    pool_of,
    rules_of,
    signal_at,
    signal_of,
    week_end_of,
    weekly_bars,
)

pytestmark = [pytest.mark.l1]

P3 = IndicatorParams(kdj_n=3, kdj_m1=3, kdj_m2=3, cci_n=3, weeks_lookback=200)


@pytest.fixture(scope="module")
def reference():
    """真源（赤潮信号层）模块——缺失即 fail（验收依赖，不静默 skip）。"""
    return load_reference()


@pytest.fixture(scope="module")
def rules(reference):
    return rules_of(reference)


@pytest.fixture(scope="module")
def params(reference):
    return params_of(reference)


@dataclass(frozen=True)
class _Bar:
    """最小 Bar 替身（date 为 YYYYMMDD 字符串）。"""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def _week_ends(count: int) -> list[str]:
    """自 2026-01-02（周五）起的连续 W-FRI 周末日。"""
    start = date(2026, 1, 2)
    return [(start + timedelta(days=7 * i)).strftime("%Y%m%d")
            for i in range(count)]


def _weekly(dates: list[str], values: list[float]):
    from btf.strategy.qidian import WeeklyBar

    return [WeeklyBar(week_end=dates[i], open=values[i], high=values[i],
                      low=values[i], close=values[i], volume=1.0)
            for i in range(len(values))]


class TestWeekEnd:
    def test_friday_is_itself(self):
        assert week_end_of("20260925") == "20260925"

    def test_monday_to_thursday_same_week(self):
        for ymd in ("20260921", "20260922", "20260923", "20260924"):
            assert week_end_of(ymd) == "20260925"

    def test_weekend_rolls_to_next_friday(self):
        """周六/周日归**下一**周五（pandas W-FRI 同口径）。"""
        assert week_end_of("20260926") == "20261002"
        assert week_end_of("20260927") == "20261002"


class TestWeeklyAggregation:
    def test_ohlcv_merge(self):
        bars = [
            _Bar("20260921", 10.0, 10.5, 9.8, 10.2, 100.0),
            _Bar("20260922", 10.2, 11.0, 10.0, 10.6, 200.0),
            _Bar("20260925", 10.6, 10.8, 10.1, 10.3, 150.0),
        ]
        weekly = weekly_bars(bars)
        assert [w.week_end for w in weekly] == ["20260925"]
        week = weekly[0]
        assert (week.open, week.high, week.low, week.close) == (10.0, 11.0, 9.8, 10.3)
        assert week.volume == pytest.approx(450.0)

    def test_cross_week_split_and_order(self):
        bars = [
            _Bar("20260925", 10.0, 10.0, 10.0, 10.0, 1.0),     # 周五 → 本周
            _Bar("20260928", 11.0, 11.0, 11.0, 11.0, 2.0),     # 下周一 → 下周五
        ]
        weekly = weekly_bars(bars)
        assert [w.week_end for w in weekly] == ["20260925", "20261002"]

    def test_unsorted_input_still_ordered(self):
        bars = [
            _Bar("20260925", 10.3, 10.8, 10.1, 10.3, 150.0),
            _Bar("20260921", 10.0, 10.5, 9.8, 10.2, 100.0),
        ]
        week = weekly_bars(bars)[0]
        assert week.open == 10.0 and week.close == 10.3       # 按日期而非入参序


class TestKdj:
    def test_insufficient_samples(self):
        assert calc_kdj([10.0], [9.0], [9.5], P3) == (50.0, 50.0, 50.0)

    def test_flat_window_rsv_50(self):
        flat = [10.0, 10.0, 10.0, 10.0]
        assert calc_kdj(flat, flat, flat, P3) == (50.0, 50.0, 50.0)

    def test_hand_computed(self):
        """n=3/m1=m2=3：逐窗口 RSV → SMA 平滑（K、D 初值 50）。"""
        high = [10.0, 11.0, 12.0, 13.0]
        low = [9.0, 10.0, 11.0, 12.0]
        close = [9.5, 10.5, 11.5, 12.5]
        k, d, j = calc_kdj(high, low, close, P3)
        assert k == pytest.approx(68.518518, abs=1e-6)
        assert d == pytest.approx(58.641975, abs=1e-6)
        assert j == pytest.approx(88.271605, abs=1e-6)
        assert j == pytest.approx(3 * k - 2 * d, abs=1e-12)


class TestCci:
    def test_insufficient_samples(self):
        assert calc_cci([10.0], [9.0], [9.5], P3) == 0.0

    def test_zero_deviation(self):
        flat = [10.0, 10.0, 10.0]
        assert calc_cci(flat, flat, flat, P3) == 0.0

    def test_hand_computed_positive(self):
        high = [10.0, 11.0, 12.0]
        low = [8.0, 9.0, 10.0]
        close = [9.0, 10.0, 11.0]
        assert calc_cci(high, low, close, P3) == pytest.approx(100.0, abs=1e-9)

    def test_hand_computed_negative(self):
        high = [12.0, 11.0, 10.0]
        low = [10.0, 9.0, 8.0]
        close = [11.0, 10.0, 9.0]
        assert calc_cci(high, low, close, P3) == pytest.approx(-100.0, abs=1e-9)


class TestSignal:
    def test_buy_by_j(self, rules):
        assert signal_of(-6.0, 0.0, rules) == BUY

    def test_buy_by_cci(self, rules):
        assert signal_of(0.0, -151.0, rules) == BUY

    def test_sell_by_j(self, rules):
        assert signal_of(111.0, 0.0, rules) == SELL

    def test_sell_by_cci(self, rules):
        assert signal_of(0.0, 151.0, rules) == SELL

    def test_none_in_middle(self, rules):
        assert signal_of(50.0, 0.0, rules) is None

    def test_buy_priority(self, rules):
        """买入优先（真源 buy_triggers 先判）：J 超卖且 CCI 超买 → 买入。"""
        assert signal_of(-10.0, 200.0, rules) == BUY
        assert signal_of(200.0, -160.0, rules) == BUY

    def test_dual_confirm_same_direction(self):
        assert dual_signal(BUY, BUY) == BUY
        assert dual_signal(SELL, SELL) == SELL

    def test_dual_confirm_conflict_or_missing(self):
        assert dual_signal(BUY, SELL) is None
        assert dual_signal(BUY, None) is None
        assert dual_signal(None, None) is None


class TestSignalAt:
    """序列级双确认（截断至指定周，无前视）。"""

    def test_insufficient_history_returns_none(self, rules, params):
        series = _weekly(_week_ends(3), [10.0, 9.5, 9.0])
        assert signal_at(series, series, rules, params,
                         series[-1].week_end) is None

    def test_crash_triggers_buy_on_both_sides(self, rules, params):
        """横盘 20 周后三周急挫：CCI 深负 + J 超卖 → 双确认买入。"""
        count = params.cci_n + 5
        values = [100.0] * (count - 3) + [90.0, 80.0, 70.0]
        series = _weekly(_week_ends(count), values)
        assert signal_at(series, series, rules, params,
                         series[-1].week_end) == BUY

    def test_rally_triggers_sell_on_both_sides(self, rules, params):
        """横盘后三周急拉：CCI 超买 + J 高位 → 双确认卖出。"""
        count = params.cci_n + 5
        values = [100.0] * (count - 3) + [110.0, 120.0, 130.0]
        series = _weekly(_week_ends(count), values)
        assert signal_at(series, series, rules, params,
                         series[-1].week_end) == SELL

    def test_no_lookahead_before_signal_week(self, rules, params):
        """信号周之前的截断查询不受后续急挫影响（无前视）。"""
        count = params.cci_n + 5
        values = [100.0] * (count - 3) + [90.0, 80.0, 70.0]
        series = _weekly(_week_ends(count), values)
        early = series[params.cci_n].week_end
        assert signal_at(series, series, rules, params, early) is None


class TestReferenceSource:
    """真源加载纪律（B2）：清单/阈值一律运行时读取，btf 零副本。"""

    def test_pool_loaded_from_reference(self, reference):
        pool = pool_of(reference)
        assert len(pool) >= 40
        assert sum(1 for info in pool.values() if info["recommended"]) == 14

    def test_groups_split_14_23_4_2(self, reference):
        groups = pool_groups()
        assert len(groups["recommended"]) == 14
        assert len(groups["dual_backtest"]) == 23          # B2：23 只名单
        assert len(groups["industry_scan"]) == 4
        assert len(groups["convertible_bond"]) == 2
        pool = pool_of(reference)
        merged = [code for codes in groups.values() for code in codes]
        assert sorted(merged) == sorted(pool)              # 分组完整覆盖池
        assert not set(groups["recommended"]) & set(groups["dual_backtest"])

    def test_convertible_bond_group_t0_members(self):
        """B3 前置：可转债组为 511180/511380（子类 BOND → T+0）。"""
        from btf.domain.types import AssetClass, EtfSubclass, Instrument, rule_for

        for code in pool_groups()["convertible_bond"]:
            inst = Instrument(code, AssetClass.ETF, name="可转债ETF",
                              etf_subclass=EtfSubclass.BOND)
            assert rule_for(inst).t_plus == 0

    def test_thresholds_and_params_come_from_reference(self, reference):
        rules = rules_of(reference)
        assert (rules.buy_j, rules.buy_cci) == (
            reference.BUY_J_THRESHOLD, reference.BUY_CCI_THRESHOLD)
        assert (rules.sell_j, rules.sell_cci) == (
            reference.SELL_J_THRESHOLD, reference.SELL_CCI_THRESHOLD)
        params = params_of(reference)
        assert (params.kdj_n, params.kdj_m1, params.kdj_m2) == (
            reference.KDJ_N, reference.KDJ_M1, reference.KDJ_M2)
        assert params.cci_n == reference.CCI_N

    def test_no_copied_pool_codes_in_btf_source(self, reference):
        """纪律机检：btf 信号层源码不得出现池内任何代码字面量。"""
        from btf.strategy import qidian as qidian_module

        source = Path(qidian_module.__file__).read_text(encoding="utf-8")
        leaked = [code for code in pool_of(reference) if code in source]
        assert not leaked, f"清单被复制进 btf 源码：{leaked}"

    def test_missing_reference_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="奇点信号源缺失"):
            load_reference(tmp_path / "nope.py")

    def test_reference_path_default(self):
        assert QIDIAN_REF.name == "qidian.py"
