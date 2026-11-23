# -*- coding: utf-8 -*-
"""R2203 G1-1：`bt data quality` 的 ST 名单接线（裁定 #6）。

覆盖三件事：
  1. `derive_st_symbols` 从 `namechange` 的 PIT 区间推导；
  2. 源表缺失 → `unavailable`（调用方必须 WARN，不得静默）；
  3. `run_data_quality` 把 `st_symbols` 原样透传给 `run_checks`；
  4. CLI 的 `--st-symbols` 与 `--no-st` 互斥（rc=2）。
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
from btf.app.services import derive_st_symbols, run_data_quality
from btf.cli.main import main

DAYS = ["20240102", "20240103", "20240104"]


def _write_trade_cal(root) -> None:
    d = root / "metadata"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"exchange": "SSE", "cal_date": day, "is_open": 1, "pretrade_date": day}
                for day in DAYS
            ]
        ),
        d / "trade_cal.parquet",
    )


def _write_namechange(root, rows: list[dict]) -> None:
    d = root / "namechange"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "2024.parquet")


def test_derive_st_symbols_pit(tmp_path):
    root = tmp_path / "market"
    _write_trade_cal(root)
    _write_namechange(
        root,
        [
            {
                "ts_code": "600000.SH",
                "name": "ST浦发",
                "start_date": "20240101",
                "end_date": "20240103",
            },
            {
                "ts_code": "600001.SH",
                "name": "*ST示例",
                "start_date": "20240103",
                "end_date": None,
            },
        ],
    )
    source, pairs = derive_st_symbols(root, "20240102", "20240104", None)
    assert source == "namechange"
    assert ("600000.SH", "20240102") in pairs
    assert ("600000.SH", "20240103") not in pairs  # end_date 为开区间（既有语义）
    assert ("600001.SH", "20240103") in pairs
    assert ("600001.SH", "20240104") in pairs


def test_derive_st_symbols_unavailable(tmp_path):
    root = tmp_path / "market"
    _write_trade_cal(root)  # 只有日历、没有 namechange
    source, pairs = derive_st_symbols(root, "20240102", "20240104", None)
    assert source == "unavailable"
    assert pairs == set()


def test_run_data_quality_forwards_st_symbols(tmp_path, monkeypatch):
    root = tmp_path / "market"
    for table in ("daily", "stk_limit"):
        d = root / table
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"ts_code": []}), d / "2024.parquet")
    captured = {}

    def fake_run_checks(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("btf.data.quality.run_checks", fake_run_checks)
    sent = {("600000.SH", "20240102")}
    run_data_quality(root, "20240102", "20240104", st_symbols=sent)
    assert captured["st_symbols"] == sent


def test_cli_st_flags_are_mutually_exclusive():
    rc = main(
        [
            "data",
            "quality",
            "--start",
            "20240102",
            "--end",
            "20240104",
            "--st-symbols",
            "600000.SH",
            "--no-st",
        ]
    )
    assert rc == 2
