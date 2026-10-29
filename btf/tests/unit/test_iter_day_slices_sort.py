# -*- coding: utf-8 -*-
"""L1 单元：**A-2-T2** `iter_day_slices` 非升序回退保持（19 号 §181 A-2）。

`ts_code` 非全局升序时必须走**原双键排序**（不得走 int32 快路径——次键稳定性
未证）。本用例用合成 tmp 数据集把两条分支都钉死：
- 升序：快路径（int32 键）输出 ≡ 单键 `(date)` 排序后的分组；
- 非升序：回退双键 `(date, ts_code)`，输出 ≡ 原实现。

注：`iter_day_slices` 的 `cols` 是**整年**列数据，日切片由 `lo/hi` 表达。
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.data.parquet_reader import iter_day_slices

pytestmark = [pytest.mark.l1]

DATES = ["20240102"] * 3 + ["20240103"] * 2 + ["20240104"] * 2


def _write(root: Path, codes: list[str]) -> Path:
    t = pa.table({"ts_code": pa.array(codes), "trade_date": pa.array(DATES),
                  "close": pa.array([float(i) for i in range(len(codes))])})
    (root / "daily").mkdir(parents=True)
    pq.write_table(t, root / "daily" / "2024.parquet")
    return t


def _reference(t, keys):
    """原实现的等价重算：排序 → 整年 cols ＋ 每日 (lo, hi)。"""
    s = t.sort_by(keys)
    cols = {c: s.column(c).to_pylist() for c in s.column_names}
    dates = cols["trade_date"]
    out: list[tuple[str, dict[str, list], int, int]] = []
    i, n = 0, len(dates)
    while i < n:
        j = i
        while j < n and dates[j] == dates[i]:
            j += 1
        out.append((dates[i], cols, i, j))
        i = j
    return out


def _slice(cols: dict[str, list], lo: int, hi: int) -> dict[str, list]:
    return {k: v[lo:hi] for k, v in cols.items()}


def test_ascending_fast_path_matches_single_key(tmp_path):
    codes = ["000001.SZ", "000002.SZ", "000003.SZ",
             "000002.SZ", "000003.SZ", "000001.SZ", "000002.SZ"]
    t = _write(tmp_path, sorted(codes))
    got = list(iter_day_slices("daily", tmp_path, "20240101", "20240131"))
    want = _reference(t, [("trade_date", "ascending")])
    assert [g[0] for g in got] == [w[0] for w in want]
    for (gy, gc, glo, ghi), (wy, wc, wlo, whi) in zip(got, want, strict=True):
        assert gy == wy
        assert (glo, ghi) == (wlo, whi)
        assert _slice(gc, glo, ghi) == _slice(wc, wlo, whi)


def test_non_ascending_falls_back_to_double_key(tmp_path):
    """`ts_code` 非升序 ⇒ 双键回退，结果与**原实现**逐值一致。"""
    codes = ["000003.SZ", "000002.SZ", "000001.SZ",
             "000003.SZ", "000001.SZ", "000002.SZ", "000001.SZ"]
    t = _write(tmp_path, codes)
    got = list(iter_day_slices("daily", tmp_path, "20240101", "20240131"))
    want = _reference(t, [("trade_date", "ascending"), ("ts_code", "ascending")])
    assert [g[0] for g in got] == [w[0] for w in want]
    for (gy, gc, glo, ghi), (wy, wc, wlo, whi) in zip(got, want, strict=True):
        assert gy == wy
        assert (glo, ghi) == (wlo, whi)
        assert _slice(gc, glo, ghi) == _slice(wc, wlo, whi)
