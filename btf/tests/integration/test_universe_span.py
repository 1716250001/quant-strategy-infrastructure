# -*- coding: utf-8 -*-
"""L4 集成：`source=all` 宇宙**期间并集**（19 号 §20.3 / P0-NEW-4）。

背景（第三轮审查头号发现）：`universe(date)` 本身逐日动态（`list_date ≤ d ≤
delist_date`），但 `source=all` 原只在**起点**求值一次 → 期间新上市标的全程
不可见（十年实测 2,847 只 = 期末宇宙 52.4%）。传导路径是**静默**的：策略选股
走基本面库（不受截面限制）选得出，`ctx.data`/撮合却买不到 → 零报错、零拒单、
零披露；与指数对比时指数含新股而回测不含 → 相对收益系统性失真。

本测试钉住三件事：
    ① `feed.universe_span` = 期间并集闭式（`⊇ 起点` 且 `⊇ 期末`）；
    ② 期间新上市标的（科创板首批 688001.SH，2019-07-22）**必须可见**；
    ③ runtime 装配 `source=all` 走期间并集并**强制披露**跨期新增数量。

注：既有验收之所以抓不到，是因为它们只断言 `fills > 100` / `n_days > 800`
（有成交即绿）——故本测试断言"宇宙完整性"这一独立维度（X-12 铁律同族）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l4]

ROOT = Path(MARKET_DATA_DIR)
requires_mainlib = pytest.mark.skipif(
    not (ROOT / "stock_basic").is_file() and not (ROOT / "daily").is_dir(),
    reason=f"主库不可用: {ROOT}")

# 十年窗口（与审查 §20.3.2 同口径：2016-01-04 → 2025-09-23）
START_YMD, END_YMD = "20160104", "20250923"
#: 期间新上市锚点：科创板首批（2019-07-22 上市）——起点宇宙必不含
MID_IPO = "688001.SH"


@requires_mainlib
class TestUniverseSpan:
    def test_span_is_union_of_period(self):
        """并集 ⊇ 起点 ∪ 期末（闭式：`list≤end ∧ (delist 空 ∨ delist≥start)`）。"""
        from btf.data.feed import TushareParquetFeed

        feed = TushareParquetFeed(ROOT)
        start, end = TradingDate.from_ymd(START_YMD), TradingDate.from_ymd(END_YMD)
        at_start = {i.symbol for i in feed.universe(start)}
        at_end = {i.symbol for i in feed.universe(end)}
        span = {i.symbol for i in feed.universe_span(start, end)}

        assert at_start <= span, "并集须覆盖起点宇宙"
        assert at_end <= span, "并集须覆盖期末宇宙"
        assert len(span) >= len(at_end)

    def test_mid_period_ipo_becomes_visible(self):
        """★ 核心：期间新上市标的必须可见（修复前全程缺席且零披露）。"""
        from btf.data.feed import TushareParquetFeed

        feed = TushareParquetFeed(ROOT)
        start, end = TradingDate.from_ymd(START_YMD), TradingDate.from_ymd(END_YMD)
        at_start = {i.symbol for i in feed.universe(start)}
        span = {i.symbol for i in feed.universe_span(start, end)}

        assert MID_IPO not in at_start, "锚点须为**期间**新上市（否则断言无意义）"
        assert MID_IPO in span, (
            f"{MID_IPO}（期间新上市）仍不可见 → 宇宙在起点冻结回归（P0-NEW-4）")

    def test_span_single_read_is_exact(self):
        """并集 = ∃d∈[start,end] 在市——与"逐日并集"同解（抽样日期校验）。"""
        from btf.data.feed import TushareParquetFeed

        feed = TushareParquetFeed(ROOT)
        start, end = TradingDate.from_ymd(START_YMD), TradingDate.from_ymd(END_YMD)
        span = {i.symbol for i in feed.universe_span(start, end)}
        # 抽样若干日期：其当日宇宙必为并集子集（并集是超集，非逐日切换）
        for ymd in ("20170103", "20190102", "20210930", END_YMD):
            day = {i.symbol for i in feed.universe(TradingDate.from_ymd(ymd))}
            assert day <= span, f"{ymd} 当日宇宙不在期间并集内（闭式有误）"


@requires_mainlib
class TestRuntimeAllUniverse:
    def test_source_all_uses_period_union_and_discloses(self):
        """runtime 装配 `source=all`：宇宙 = 期间并集 + **强制披露**跨期新增。"""
        from btf.runtime import BTFRuntime

        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "000001.SZ", "weight": 0.0},
                "universe": {"source": "all"},
                "period": {"start": "2016-01-04", "end": "2025-09-23"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": "tushare_parquet"},
            # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
            "risk": {"rules": [], "allow_empty_chain": True},
        }
        rt = BTFRuntime().load_config(cfg).build()
        at_start = len(rt.feed.universe(TradingDate.from_ymd(START_YMD)))

        assert len(rt.instruments) > at_start, (
            f"source=all 宇宙未跨期扩张（{len(rt.instruments)} ≤ 起点 {at_start}）"
            f"—— 起点冻结回归（P0-NEW-4）")
        assert MID_IPO in rt.instruments, f"{MID_IPO} 期间新上市仍缺席"
        notes = " ".join(rt.assembly_notes)
        assert "跨期新增" in notes and str(len(rt.instruments)) in notes, (
            f"装配期未披露跨期新增数量：{rt.assembly_notes}")
