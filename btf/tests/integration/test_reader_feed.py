# -*- coding: utf-8 -*-
"""L2 集成测试：Parquet 读取器 + Feed 单位换算（PoC-1 任务 1.2/1.4/1.5 验收）。

依赖真实主库（D:\\全量数据\\market_data）——只读。
验收锚点（18 号计划 1.2/1.4/1.5）：双布局读取、谓词下推、量纲合理性、
glob 陷阱防护、截面迭代升序。
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import TushareParquetFeed
from btf.data.parquet_reader import ReaderError, read_codes, read_full, read_range
from btf.data.tables_meta import TABLES
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l2]

ROOT = Path(MARKET_DATA_DIR)


def _skip_if_no_data():
    if not (ROOT / "daily").is_dir():
        pytest.skip("主库不可用（BTF_DATA_DIR 未指向数据盘）")


class TestTablesMeta:
    """17 表登记与架构字典一致（05 §9.4，含 E3/E4 勘误值）。"""

    def test_tables_registered(self):
        """05 §9.4 字典编号 17 项（T-01..T-17，其中 T-12=fund_daily+etf_limit
        两张物理表）→ 物理表 18 张。"""
        assert len(TABLES) == 18
        for required in (
            "daily", "adj_factor", "stk_limit", "suspend_d", "namechange",
            "dividend", "stock_basic", "trade_cal", "index_daily", "daily_basic",
            "index_weight", "index_dailybasic", "new_share", "moneyflow",
            "fina_indicator", "fx_daily", "fund_daily", "etf_limit",
        ):
            assert required in TABLES, f"核心表缺失: {required}"

    def test_eratta_start_dates(self):
        """终审勘误锚点：E3 etf_limit=20190626、E4 moneyflow=20080102。"""
        assert TABLES["etf_limit"].start == "20190626"
        assert TABLES["moneyflow"].start == "20080102"
        assert TABLES["daily"].start == "19901219"
        assert TABLES["stk_limit"].start == "20080102"

    def test_daily_unit_conversions_declared(self):
        convs = {c.field: c for c in TABLES["daily"].conversions}
        assert convs["vol"].factor == 100.0      # 手 → 股
        assert convs["amount"].factor == 1000.0  # 千元 → 元


class TestReader:
    def test_read_full_single_layout(self):
        _skip_if_no_data()
        t = read_full("stock_basic", ROOT, columns=["ts_code", "list_status"])
        assert t.num_rows > 5000  # 实测 5,882

    def test_predicate_pushdown_single_code(self):
        """谓词下推：单标的单年读取行数≈交易日数（实测 000001.SZ 2015=244）。"""
        _skip_if_no_data()
        t = read_codes("daily", ROOT, ["000001.SZ"], "20150101", "20151231")
        assert 240 <= t.num_rows <= 250

    def test_range_filter_boundary(self):
        """区间边界含端点。"""
        _skip_if_no_data()
        t = read_range("daily", ROOT, "20150105", "20150109",
                       columns=["ts_code", "trade_date"])
        dates = set(t.column("trade_date").to_pylist())
        assert dates == {"20150105", "20150106", "20150107", "20150108", "20150109"}

    def test_unregistered_table_rejected(self):
        with pytest.raises(ReaderError):
            read_full("no_such_table", ROOT)

    def test_layout_violation_detected(self, tmp_path: Path):
        """by_year 目录混入非年份文件 → 拒绝（防静默漏读）。"""
        fake = tmp_path / "daily"
        fake.mkdir()
        (fake / "2015.parquet").touch()
        (fake / "notes.parquet").touch()
        with pytest.raises(ReaderError):
            read_range("daily", tmp_path, "20150101", "20151231")

    def test_missing_dir_rejected(self, tmp_path: Path):
        with pytest.raises(ReaderError):
            read_range("daily", tmp_path, "20150101", "20151231")


class TestFeedUnits:
    """1.4 单位换算契约（量纲断言：评审 C1 数据约束对策）。"""

    def test_bar_units_stock(self):
        _skip_if_no_data()
        feed = TushareParquetFeed(ROOT)
        bars = feed.bars_of("000001.SZ",
                            TradingDate(date(2015, 1, 5)), TradingDate(date(2015, 1, 9)))
        assert len(bars) == 5
        b = bars[0]
        # 实测原始：vol=2,860,436.43 手 → 股；amount=4,565,387.8464 千元 → 元
        assert math.isclose(b.volume, 286_043_643.0, rel_tol=1e-6)
        assert math.isclose(b.amount, 4_565_387_846.4, rel_tol=1e-6)
        # 量纲合理性：均价 = amount / volume 落在价格量级（而非手/千元口径的 100 倍错位）
        avg_px = b.amount / b.volume
        assert 1.0 < avg_px < 100.0, f"均价 {avg_px} 异常——单位换算违例"

    def test_bar_cross_section_iter_ascending(self):
        _skip_if_no_data()
        feed = TushareParquetFeed(ROOT)
        it = feed.bars(None, TradingDate(date(2015, 1, 5)), TradingDate(date(2015, 1, 12)))
        dates = []
        for td, m in it:
            dates.append(td.to_ymd())
            assert all(r.symbol in m for r in m.values())  # map 键一致性
        assert dates == sorted(dates)  # 升序
        assert len(dates) == 6          # 2015-01-05..12 共 6 个交易日

    def test_symbol_filter(self):
        _skip_if_no_data()
        feed = TushareParquetFeed(ROOT)
        it = feed.bars(["000001.SZ"], TradingDate(date(2015, 1, 5)), TradingDate(date(2015, 1, 9)))
        for _, m in it:
            assert set(m.keys()) == {"000001.SZ"}

    def test_etf_feed_fund_daily(self):
        """ETF 日线（fund_daily，H1 验收场景数据源）。"""
        _skip_if_no_data()
        if not (ROOT / "fund_daily").is_dir():
            pytest.skip("fund_daily 不可用")
        feed = TushareParquetFeed(ROOT)
        bars = feed.bars_of("510300.SH",
                            TradingDate(date(2023, 6, 1)), TradingDate(date(2023, 6, 30)),
                            daily_table="fund_daily")
        assert 15 <= len(bars) <= 25
        for b in bars:
            avg_px = b.amount / b.volume if b.volume else 0
            assert 0.5 < avg_px < 20.0  # 沪深300ETF 价格量级（约 4 元）
