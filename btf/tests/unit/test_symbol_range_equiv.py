# -*- coding: utf-8 -*-
"""KK-1：`YearTableStore.symbol_range` 谓词下推等价性（19 号 §132.1）。

护栏（铁律新 15：防线须可被证伪）：
    ① 同符号同区间：新路径（`read_year_where` 下推）与旧路径（整年 `load` +
       过滤）**内容逐位一致**（按 `trade_date` 排序后比较）；
    ② 边界年（y0/y1）日期上下界过滤仍生效；
    ③ **无文件年早退**：扫描 1990–2099 时 `pq.read_table` 调用次数 = 有文件年数；
    ④ 未登记表仍抛 `ValueError`（签名/失败语义不变）。
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from btf.data import core
from btf.data.core import YearTableStore

TABLE = "adj_factor"
SYMS = ("600000.SH", "000001.SZ")


def _row(sym: str, day: str, factor: float) -> dict:
    return {"ts_code": sym, "trade_date": day, "adj_factor": factor}


def _write_year(root: Path, year: int, days: list[str]) -> None:
    rows = []
    for i, day in enumerate(days):
        for j, sym in enumerate(SYMS):
            rows.append(_row(sym, day, 1.0 + i * 0.01 + j * 0.001))
    pq.write_table(pa.Table.from_pylist(rows), root / TABLE / f"{year}.parquet")


def _rows(table: pa.Table) -> list[dict]:
    return sorted(table.to_pylist(), key=lambda x: (x["trade_date"], x["ts_code"]))


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    r = tmp_path / "market"
    (r / TABLE).mkdir(parents=True)
    _write_year(r, 2019, ["20190102", "20191231"])
    _write_year(r, 2020, ["20200102", "20200601", "20201231"])
    _write_year(r, 2021, ["20210104", "20211231"])
    return r


class TestSymbolRangeEquivalent:
    def test_full_span_content_matches_old_path(self, root: Path):
        store = YearTableStore(root)
        cols = ["adj_factor"]
        new = store.symbol_range(TABLE, SYMS[0], "20190102", "20211231", cols)

        parts = []
        for year in (2019, 2020, 2021):
            t = store.load(TABLE, year, ("adj_factor", "ts_code", "trade_date"))
            assert t is not None
            parts.append(t.filter(pc.equal(t.column("ts_code"), SYMS[0])))
        old = pa.concat_tables(parts)

        assert _rows(new) == _rows(old)
        assert new.num_rows == 7

    def test_boundary_years_filter(self, root: Path):
        store = YearTableStore(root)
        t = store.symbol_range(TABLE, SYMS[0], "20200601", "20201231",
                               ["adj_factor"])
        assert [r["trade_date"] for r in _rows(t)] == ["20200601", "20201231"]

    def test_single_day(self, root: Path):
        store = YearTableStore(root)
        t = store.symbol_range(TABLE, SYMS[1], "20200102", "20200102",
                               ["adj_factor"])
        rows = _rows(t)
        assert len(rows) == 1
        assert rows[0]["ts_code"] == SYMS[1]
        assert rows[0]["trade_date"] == "20200102"

    def test_missing_file_years_early_exit(self, root: Path, monkeypatch):
        store = YearTableStore(root)
        real_read = pq.read_table
        calls: list[tuple] = []

        def counted(*args, **kwargs):
            calls.append(args)
            return real_read(*args, **kwargs)

        monkeypatch.setattr(core.pq, "read_table", counted)
        t = store.symbol_range(TABLE, SYMS[0], "19900101", "20991231",
                               ["adj_factor"])
        assert t.num_rows == 7
        assert len(calls) == 3, f"仅 3 个有文件年应读盘，实际 {len(calls)}"

    def test_unregistered_table_raises(self, root: Path):
        store = YearTableStore(root)
        with pytest.raises(ValueError, match="表未登记"):
            store.symbol_range("no_such_table", SYMS[0], "20200101", "20201231",
                               ["x"])
