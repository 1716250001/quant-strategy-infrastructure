# -*- coding: utf-8 -*-
"""L1 单元：**退市代码 `T` 前缀路由**（19 号 §183 B-3）。

tushare 对已退市股票用 `T` + 原 6 位代码（`T600018.SH` = 「上港集箱(退)」，
`list_date=20000719`）。该前缀不在 `STOCK_PREFIXES`，原实现落到 `fund_daily`
兜底 ⇒ `source: all` 宇宙混入"需 fund_daily 的股票" ⇒ `guard_feed_routing` 抛
`ConfigError`。修法：`daily_table_of` 在 `partition` 后剥离 `T` 前缀再判定。

**连带语义（不隐去）**：`factor_table_of` 复用 `daily_table_of` ⇒ T 前缀标的
复权因子由 `fund_adj` 转 `adj_factor`（它本就是股票，更正确）。
"""
from __future__ import annotations

import pytest
from btf.data.tables_meta import daily_table_of, factor_table_of

pytestmark = [pytest.mark.l1]

#: 改动前判定（基线；6 个非 T 代码必须逐一致）
BEFORE = {
    "600018.SH": "daily",
    "000001.SZ": "daily",
    "510300.SH": "fund_daily",
    "159915.SZ": "fund_daily",
    "000300.SH": "index_daily",    # 沪市 000xxx 为指数段（上证指数）
    "399001.SZ": "index_daily",
}


def test_t1_delisted_sh_routes_daily():
    """T-1：沪市退市股剥离 T 前缀后判为股票。"""
    assert daily_table_of("T600018.SH") == "daily"


def test_t2_delisted_sz_routes_daily():
    """T-2：深市退市股同上。"""
    assert daily_table_of("T000001.SZ") == "daily"


def test_t3_non_t_codes_unchanged():
    """T-3：6 个非 T 代码判定与改动前逐一致（不得回归）。"""
    got = {sym: daily_table_of(sym) for sym in BEFORE}
    assert got == BEFORE


def test_t4_factor_table_follows_daily_routing():
    """T-4：连带语义——退市股复权因子表由 `fund_adj` 转 `adj_factor`。"""
    assert factor_table_of("T600018.SH") == "adj_factor"
