# -*- coding: utf-8 -*-
"""L2 集成：**A-3-T1** `state` 两处索引等价（19 号 §181 A-3）。

`_limit_date_index` / `_close_date_index` 改用 `core.sort_by_date_int32` 后，
排序后的年表与 `{ymd: (lo, hi)}` 索引必须与**原实现**（`large_string` 排序）逐
键＋逐行相等。样本年 2024（主库真实数据）。
"""
from __future__ import annotations

import pyarrow.compute as pc
import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.state import StateSynthesizer

pytestmark = [pytest.mark.l2]

requires_mainlib = pytest.mark.skipif(
    not (MARKET_DATA_DIR / "daily").is_dir()
    or not (MARKET_DATA_DIR / "stk_limit").is_dir(),
    reason=f"主库不可用: {MARKET_DATA_DIR}")

YEAR = 2024


def _reference(t):
    """原实现的等价重算：string 单键排序 + group_by 计数 → 索引。"""
    s = t.take(pc.sort_indices(t, sort_keys=[("trade_date", "ascending")]))
    grouped = s.group_by("trade_date").aggregate([("ts_code", "count")])
    ordered = grouped.sort_by("trade_date")
    index: dict[str, tuple[int, int]] = {}
    start = 0
    for day, count in zip(ordered.column("trade_date").to_pylist(),
                          ordered.column("ts_code_count").to_pylist(),
                          strict=True):
        index[day] = (start, start + count)
        start += count
    return s, index


@pytest.fixture(scope="module")
def synth() -> StateSynthesizer:
    return StateSynthesizer(MARKET_DATA_DIR)


@requires_mainlib
def test_limit_date_index_equivalent(synth):
    orig = synth._limit_table(YEAR)
    if orig is None:
        pytest.skip(f"stk_limit/{YEAR} 缺失")
    ref_sorted, ref_index = _reference(orig)
    got_sorted, got_index = synth._limit_date_index(YEAR)
    assert got_index == ref_index
    assert got_sorted.equals(ref_sorted)


@requires_mainlib
def test_close_date_index_equivalent(synth):
    orig = synth._close_table(YEAR)
    if orig is None:
        pytest.skip(f"daily/{YEAR} 缺失")
    ref_sorted, ref_index = _reference(orig)
    got_sorted, got_index = synth._close_date_index(YEAR)
    assert got_index == ref_index
    assert got_sorted.equals(ref_sorted)
