# -*- coding: utf-8 -*-
"""混合表 Feed 与标的路由单测（V3-1；M3 任务 6.3）。

覆盖：
    1. 标的路由三类（股票 / ETF·LOF / 指数）+ **沪市指数段 vs 深市股票**歧义；
    2. 多表截面合并视图（跨表命中 / 缺席 / keys 并集 / 先命中者胜）；
    3. 混合表使用纪律：拒绝全市场截面、拒绝表覆盖、`requires_explicit_symbols`。
"""
from __future__ import annotations

import pytest
from btf.data.feed import FeedError, MixedDailyFeed, _MergedCrossSection
from btf.data.tables_meta import daily_table_of
from btf.domain.market import Bar
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l1]


def _bar(symbol: str, ymd: str, close: float) -> Bar:
    day = TradingDate.from_ymd(ymd)
    return Bar(symbol=symbol, date=day, open=close, high=close, low=close,
               close=close, volume=1.0, amount=1.0, pre_close=close)


class TestRouting:
    def test_stock_routes(self):
        for symbol in ("000001.SZ", "002415.SZ", "300750.SZ", "600000.SH",
                       "688981.SH", "830799.BJ", "900901.SH"):
            assert daily_table_of(symbol) == "daily", symbol

    def test_etf_and_lof_routes(self):
        for symbol in ("511380.SH", "560700.SH", "159995.SZ", "160639.SZ",
                       "501030.SH", "589070.SH", "515220.SH"):
            assert daily_table_of(symbol) == "fund_daily", symbol

    def test_index_routes(self):
        for symbol in ("000832.CSI", "980022.SZ", "H30315.CSI", "399431.SZ",
                       "931479.CSI", "000001.SH", "000300.SH", "950041.CSI"):
            assert daily_table_of(symbol) == "index_daily", symbol

    def test_sh_index_vs_sz_stock_ambiguity(self):
        """`000xxx` 段歧义：000001.SH=上证指数 / 000001.SZ=平安银行。"""
        assert daily_table_of("000001.SH") == "index_daily"
        assert daily_table_of("000001.SZ") == "daily"


class TestMergedCrossSection:
    def test_lookup_across_sections(self):
        stock = {"000001.SZ": _bar("000001.SZ", "20260923", 10.0)}
        fund = {"511380.SH": _bar("511380.SH", "20260923", 13.0)}
        merged = _MergedCrossSection([stock, fund])
        assert merged["000001.SZ"].close == 10.0
        assert merged.get("511380.SH").close == 13.0
        assert merged.get("999999.SZ") is None
        assert sorted(merged.keys()) == ["000001.SZ", "511380.SH"]
        assert len(merged) == 2
        assert "511380.SH" in merged and "999999.SZ" not in merged

    def test_missing_symbol_raises(self):
        merged = _MergedCrossSection([{}])
        with pytest.raises(KeyError):
            merged["000001.SZ"]

    def test_first_section_wins(self):
        first = {"X.SH": _bar("X.SH", "20260923", 1.0)}
        second = {"X.SH": _bar("X.SH", "20260923", 2.0)}
        assert _MergedCrossSection([first, second])["X.SH"].close == 1.0


class TestMixedFeedDiscipline:
    def test_bars_requires_symbols(self):
        """混合表无「全市场截面」语义——须显式 symbols（显式报错，不静默）。"""
        feed = MixedDailyFeed()
        day = TradingDate.from_ymd("20260923")
        with pytest.raises(FeedError, match="全市场截面"):
            list(feed.bars(None, day, day))

    def test_bars_rejects_table_override(self):
        feed = MixedDailyFeed()
        day = TradingDate.from_ymd("20260923")
        with pytest.raises(FeedError, match="自动路由"):
            list(feed.bars(["511380.SH"], day, day, daily_table="fund_daily"))

    def test_declares_explicit_symbols_requirement(self):
        assert MixedDailyFeed.requires_explicit_symbols is True

    def test_registered_in_registry(self):
        from btf import registry

        assert "mixed" in registry.available(registry.DATA_FEED)
