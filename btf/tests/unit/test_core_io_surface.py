# -*- coding: utf-8 -*-
"""EX-4 完全体：`core.YearTableStore` 的 IO 出口（19 号 §47.4 余项 ② / §49）。

纪律（`bt check` 第 9 项机检 + import-linter 新 9 双重守卫）：
    · `btf/` 内**只有** `data/parquet_reader.py`（实现）与 `data/core.py`
      （唯一消费者）可触磁盘；
    · 其余模块一律经 `core.YearTableStore` 的 9 个出口方法取数。

本文件断言（铁律新 15：防线须可被证伪）：
    ① **语义等值**：`core` 的委托方法与其底层 `parquet_reader` 实现**返回完全
       相同的表**（逐行逐列）——防"收敛过程中悄悄改口径"；
    ② **缺失语义**：`read_file` → None、`schema_names` → []（不抛），与迁移前
       `path.is_file()` 守卫逐位一致；
    ③ **元数据档**：`file_metas`/`file_stems` 与 reader 同源（指纹输入不漂移）。
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.data import parquet_reader as pr
from btf.data.core import YearTableStore

DAYS = ["20240102", "20240103", "20240104"]
SYM = "600000.SH"


def _rows(day: str, close: float = 10.0) -> dict:
    return {"ts_code": SYM, "trade_date": day, "open": close, "high": close,
            "low": close, "close": close, "pre_close": close,
            "vol": 1000.0, "amount": close * 1000.0}


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """合成主库：daily（by_year）+ stock_basic（metadata）+ 缺 suspend_d。"""
    r = tmp_path / "market"
    (r / "daily").mkdir(parents=True)
    (r / "metadata").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_rows(d) for d in DAYS]),
                   r / "daily" / "2024.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": SYM, "name": "浦发银行", "list_date": "19991110",
         "delist_date": None}]), r / "metadata" / "stock_basic.parquet")
    return r


class TestSemanticEquivalence:
    """① 委托实现与 reader **逐行等价**（收敛不改口径）。"""

    def test_read_range_matches_reader(self, root: Path):
        store = YearTableStore(root)
        cols = ["ts_code", "trade_date", "close"]
        a = store.read_range("daily", "20240102", "20240104", columns=cols)
        b = pr.read_range("daily", root, "20240102", "20240104", columns=cols)
        assert a.to_pylist() == b.to_pylist()

    def test_read_full_matches_reader(self, root: Path):
        store = YearTableStore(root)
        a = store.read_full("stock_basic", columns=["ts_code", "list_date"])
        b = pr.read_full("stock_basic", root, columns=["ts_code", "list_date"])
        assert a.to_pylist() == b.to_pylist()

    def test_read_codes_matches_reader(self, root: Path):
        store = YearTableStore(root)
        cols = ["ts_code", "trade_date", "close"]
        a = store.read_codes("daily", [SYM], "20240102", "20240104", columns=cols)
        b = pr.read_codes("daily", root, [SYM], "20240102", "20240104",
                          columns=cols)
        assert a.to_pylist() == b.to_pylist()

    def test_iter_day_slices_matches_reader(self, root: Path):
        store = YearTableStore(root)
        cols = ["ts_code", "trade_date", "close"]
        a = [(y, dict(c), lo, hi) for y, c, lo, hi in
             store.iter_day_slices("daily", "20240102", "20240104", columns=cols)]
        b = [(y, dict(c), lo, hi) for y, c, lo, hi in
             pr.iter_day_slices("daily", root, "20240102", "20240104",
                                columns=cols)]
        assert a == b
        assert [d for d, *_ in a] == DAYS

    def test_peek_columns_matches_reader(self, root: Path):
        store = YearTableStore(root)
        assert store.peek_columns("daily") == pr.peek_columns("daily", root)


class TestMissingSemantics:
    """② 缺失 → None / []（迁移前 `path.is_file()` 守卫的逐位等价）。"""

    def test_read_file_missing_returns_none(self, root: Path):
        store = YearTableStore(root)
        assert store.read_file("suspend_d", year=2024) is None      # 目录缺失
        assert store.read_file("daily", year=1999) is None          # 年文件缺失

    def test_schema_names_missing_returns_empty(self, root: Path):
        store = YearTableStore(root)
        assert store.schema_names("suspend_d", year=2024) == []

    def test_read_file_metadata_layout(self, root: Path):
        store = YearTableStore(root)
        t = store.read_file("stock_basic")           # metadata 布局（year 忽略）
        assert t is not None and t.num_rows == 1


class TestMetaSurface:
    """③ 元数据档与 reader 同源（版本指纹输入不漂移）。"""

    def test_file_stems_and_metas_align(self, root: Path):
        store = YearTableStore(root)
        assert store.file_stems("daily") == pr.file_stems("daily", root) == ["2024"]
        metas = store.file_metas("daily")
        assert len(metas) == 1
        fm = metas[0]
        assert fm.stem == "2024" and fm.num_rows == len(DAYS)
        assert fm.size > 0 and fm.mtime_ns > 0

    def test_file_metas_missing_table_is_empty(self, root: Path):
        """列表语义**容忍缺失**（指纹对可选表不炸）；读路径仍严格。"""
        store = YearTableStore(root)
        assert store.file_metas("suspend_d") == []

    def test_read_path_still_strict(self, root: Path):
        """读路径严格性未被列表语义削弱（目录缺失 → ReaderError 防静默漏读）。"""
        store = YearTableStore(root)
        with pytest.raises(pr.ReaderError, match="表目录缺失"):
            list(store.iter_day_slices("suspend_d", "20240102", "20240104",
                                       columns=["ts_code"]))
