# -*- coding: utf-8 -*-
"""CC-FIX-19：`FREQ_DAYS` 日历月对齐（双条件）判据。

对应 E1（漂移修复）／E2（季频）／E3（无记录）／E5（幂等）＋月末夹紧边界。
"""
from datetime import date

from config.schedule import RELEASE_DAY, SCHEDULE
from fetch.schedule_runner import _last_dom, is_due


def test_e1_ppi_drift_fixed():
    """E1：11-10 拉过 → 12-09 不再提前到期，12-10 才到期（旧等间隔会在 12-10 判 True）。"""
    assert is_due("M", "2026-11-10", today=date(2026, 12, 9), name="cn_ppi") is False
    assert is_due("M", "2026-11-10", today=date(2026, 12, 10), name="cn_ppi") is True


def test_e1_other_monthly_tables():
    for name, day, last in (("cn_cpi", 9, "2026-11-09"),
                            ("cn_m", 12, "2026-11-12"),
                            ("sf_month", 12, "2026-11-12")):
        assert is_due("M", last, today=date(2026, 12, day - 1), name=name) is False
        assert is_due("M", last, today=date(2026, 12, day), name=name) is True


def test_e2_gdp_quarter_anchor():
    """E2：季后 20 日；非发布月永不到期。"""
    lf = "2026-07-21"
    assert is_due("Q", lf, today=date(2026, 10, 19), name="cn_gdp") is False
    assert is_due("Q", lf, today=date(2026, 10, 20), name="cn_gdp") is True
    assert is_due("Q", lf, today=date(2026, 11, 20), name="cn_gdp") is False


def test_e3_no_record_always_due():
    for name in list(SCHEDULE) + list(RELEASE_DAY):
        assert is_due(SCHEDULE.get(name, "M"), None, today=date(2026, 12, 31),
                      name=name) is True


def test_e5_idempotent():
    args = ("M", "2026-11-10", date(2026, 12, 9), "cn_ppi")
    assert is_due(*args) == is_due(*args)


def test_non_calendar_tables_unchanged():
    """E4 单元级：无 RELEASE_DAY 的表仍按等间隔（90 天边界）。"""
    assert is_due("Q", "2026-01-01", today=date(2026, 3, 31), name="fina_audit") is False
    assert is_due("Q", "2026-01-01", today=date(2026, 4, 1), name="fina_audit") is True
    assert is_due("W", "2026-12-01", today=date(2026, 12, 7), name="new_share") is False
    assert is_due("W", "2026-12-01", today=date(2026, 12, 8), name="new_share") is True


def test_month_end_clamp():
    assert _last_dom(2026, 2) == 28
    assert _last_dom(2028, 2) == 29
    assert _last_dom(2026, 4) == 30


def test_release_day_config_shape():
    """E6 相邻：日历表必须在册或已登记剔除；月份列表只允许 1/4/7/10。"""
    for name, (day, months) in RELEASE_DAY.items():
        assert 1 <= day <= 31, name
        assert name in SCHEDULE or name == "cn_pmi", name
        if months:
            assert sorted(months) == [1, 4, 7, 10], name
