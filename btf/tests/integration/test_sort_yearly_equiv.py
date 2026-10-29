# -*- coding: utf-8 -*-
"""L2 集成：**A-2-T1** 逐年排序逐位等价（19 号 §181 A-2，2016–2025）。

`sort_by_date_int32` 与「原 `large_string` 单键稳定排序」在**每一年**必须
逐行逐列相等（`trade_date` + `ts_code`）。移植自诊断脚本
`赤潮/_scratch/perf-20261029/test_perf_gate.py::test_sort_semantics_equivalent`。
"""
from __future__ import annotations

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.core import YearTableStore, sort_by_date_int32

pytestmark = [pytest.mark.l2]

ROOT = MARKET_DATA_DIR
requires_mainlib = pytest.mark.skipif(
    not (ROOT / "daily").is_dir(), reason=f"主库不可用: {ROOT}")

YEARS = list(range(2016, 2026))


@pytest.fixture(scope="module")
def store() -> YearTableStore:
    return YearTableStore(ROOT)


@requires_mainlib
@pytest.mark.parametrize("year", YEARS)
def test_int32_sort_bitwise_equal_per_year(store: YearTableStore, year: int):
    t = store.read_file("daily", year=year, columns=["ts_code", "trade_date"])
    if t is None:
        pytest.skip(f"daily/{year}.parquet 缺失")
    orig = t.sort_by([("trade_date", "ascending")])
    new = sort_by_date_int32(t)
    assert orig.num_rows == new.num_rows
    for col in ("trade_date", "ts_code"):
        assert orig.column(col).equals(new.column(col)), (
            f"{year} 年 {col} 排序结果不一致——稳定性假设被打破")
