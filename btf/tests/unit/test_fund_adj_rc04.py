# -*- coding: utf-8 -*-
"""L1 单测：ETF 复权因子表登记与路由（**RC-04，2026-10-05**）。

    - `fund_adj` 登记（**18 → 19 表**；起点 19991022 —— 主库实测
      `fund_adj/1999.parquet` 最小 `trade_date`）；
    - `factor_table_of` 分支：股票 → `adj_factor` / ETF·LOF → `fund_adj`
      / 指数 → `adj_factor`（保持既有行为）；
    - `check_etf_adjust_coverage`（**条件降级**：仅 ETF 宇宙适用）；
    - `TushareAdjustService.degraded_notes()`（缺失披露通道，与
      `StateSynthesizer.degraded_notes()` 同形）。
"""
from __future__ import annotations

import pytest
from btf.data.adjust import TushareAdjustService
from btf.data.coverage import check_etf_adjust_coverage
from btf.data.tables_meta import TABLES, Layout, daily_table_of, factor_table_of

pytestmark = [pytest.mark.l1]


class TestFundAdjRegistration:
    def test_registered_and_19_tables(self):
        assert "fund_adj" in TABLES
        # RC-04：18 → 19；**CL-2-H/R32′（2026-10-07）**：19 → 20（新增基金
        # 分红表 `fund_div`——ADR-20；断言随规范变更更新，非弱化）。
        assert len(TABLES) == 20

    def test_meta_fields(self):
        meta = TABLES["fund_adj"]
        assert meta.layout is Layout.BY_YEAR
        assert meta.date_col == "trade_date"
        assert meta.start == "19991022"          # 主库实测首日
        assert meta.pk == ("ts_code", "trade_date")


class TestFactorTableRouting:
    @pytest.mark.parametrize("symbol,expected", [
        ("600519.SH", "adj_factor"),      # 沪市主板股票
        ("000001.SZ", "adj_factor"),      # 深市主板股票
        ("300750.SZ", "adj_factor"),      # 创业板
        ("688981.SH", "adj_factor"),      # 科创板
        ("900901.SH", "adj_factor"),      # B 股（daily 段）
        ("510300.SH", "fund_adj"),        # 沪市 ETF
        ("159915.SZ", "fund_adj"),        # 深市 ETF
        ("161010.SZ", "fund_adj"),        # LOF
        ("000001.SH", "adj_factor"),      # 沪市指数（不复权，行为不变）
        ("399006.SZ", "adj_factor"),      # 深市指数
    ])
    def test_routing(self, symbol, expected):
        assert factor_table_of(symbol) == expected

    def test_shares_daily_table_logic(self):
        """路由**复用 `daily_table_of`**（单一判定来源，禁第二套 = 双真源）。"""
        for symbol in ("600519.SH", "000001.SZ", "510300.SH", "159915.SZ",
                       "161010.SZ", "399006.SZ", "000001.SH"):
            want = ("fund_adj" if daily_table_of(symbol) == "fund_daily"
                    else "adj_factor")
            assert factor_table_of(symbol) == want


class TestEtfCoverageGuard:
    def test_note_when_period_before_start(self):
        note = check_etf_adjust_coverage("19950101")      # 早于 19991022
        assert note is not None
        assert "fund_adj" in note and "19991022" in note

    def test_none_when_covered(self):
        assert check_etf_adjust_coverage("20100101") is None
        assert check_etf_adjust_coverage("20200101") is None
        assert check_etf_adjust_coverage("19991022") is None    # 边界：等于起点

    def test_not_in_default_degraded_tables(self):
        """`fund_adj` **不得**无条件进 `DEGRADED_TABLES`（否则 1999 年前的
        纯股票回测会误报降级）——条件降级由 runtime 按宇宙触发。"""
        from btf.data.coverage import DEGRADED_TABLES

        assert "fund_adj" not in DEGRADED_TABLES


class TestDegradedNotes:
    def test_empty_by_default(self):
        assert TushareAdjustService().degraded_notes() == ()

    def test_missing_symbol_reported(self):
        svc = TushareAdjustService()
        svc._cache = {"510999.SH": ([], [])}          # 表内无行（回落 1.0）
        svc.missing_factor_symbols.add("510999.SH")
        svc.n_missing_fallback = 3
        notes = svc.degraded_notes()
        assert len(notes) == 1
        assert "510999.SH" in notes[0]
        assert "fund_adj" in notes[0]                 # 披露查表（路由可见）
        assert "3 次" in notes[0]
