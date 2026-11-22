# -*- coding: utf-8 -*-
"""D10 audit duplicate 外存聚合测试（REQ-20261121-06）

`audit_duplicates` 的主键重复判定已从「pd.concat 全部年度 PK 列」改为
「逐文件 GROUP BY + 临时 SQLite 外存累加」，本文件钉死：
  ① 重复数与 pandas `duplicated().sum()` 等价；
  ② 主键列全缺 → -1（旧语义）；
  ③ 临时外存文件用后即删、不留残留。
"""

import os
import tempfile

import pandas as pd

import tools.db.audit as adb


def _patch_db(monkeypatch, tmp_path, pk_map):
    monkeypatch.setattr(adb, "MARKET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(adb, "DB_AUDIT_PKEYS", pk_map)
    monkeypatch.setattr(adb, "DB_AUDIT_UPSTREAM_DUP", frozenset())
    monkeypatch.setattr(adb, "KEY_NARROW_ACK", {})


def _table(tmp_path, name, files):
    d = tmp_path / name
    d.mkdir(parents=True)
    for fname, df in files.items():
        df.to_parquet(d / fname, index=False)
    return d


def test_external_pk_dup_matches_pandas(monkeypatch, tmp_path):
    _patch_db(monkeypatch, tmp_path, {"tbl": ["k"]})
    _table(
        tmp_path,
        "tbl",
        {
            "2024.parquet": pd.DataFrame({"k": [1, 2, 2, 3], "v": list("abcd")}),
            "2025.parquet": pd.DataFrame({"k": [1, 4], "v": list("ef")}),
        },
    )

    created = []
    real_mkdtemp = tempfile.mkdtemp

    def _rec(*a, **k):
        p = real_mkdtemp(*a, **k)
        created.append(p)
        return p

    monkeypatch.setattr(tempfile, "mkdtemp", _rec)

    rows = adb.audit_duplicates(only=("tbl",), verbose=False)

    assert len(rows) == 1
    assert rows[0]["rows"] == 6
    assert rows[0]["pk_dup"] == 2, "与 pandas duplicated(subset=['k']).sum() 等价"
    assert rows[0]["verdict"].startswith("✓"), "无主键配置优先时不应误报（写路径全列判重）"

    assert created, "必须确实使用了临时外存"
    assert all(not os.path.exists(p) for p in created), "临时文件用后即删"


def test_external_pk_dup_multi_column(monkeypatch, tmp_path):
    _patch_db(monkeypatch, tmp_path, {"tbl": ["ts_code", "trade_date"]})
    _table(
        tmp_path,
        "tbl",
        {
            "2024.parquet": pd.DataFrame(
                {
                    "ts_code": ["A", "A", "B"],
                    "trade_date": ["20240102", "20240102", "20240103"],
                }
            ),
            "2025.parquet": pd.DataFrame(
                {
                    "ts_code": ["A", "B"],
                    "trade_date": ["20240102", "20240103"],
                }
            ),
        },
    )

    rows = adb.audit_duplicates(only=("tbl",), verbose=False)
    assert rows[0]["rows"] == 5
    assert rows[0]["pk_dup"] == 3


def test_external_pk_missing_columns_returns_minus_one(monkeypatch, tmp_path):
    _patch_db(monkeypatch, tmp_path, {"tbl": ["k"]})
    _table(
        tmp_path,
        "tbl",
        {
            "2024.parquet": pd.DataFrame({"z": [1, 2]}),
            "2025.parquet": pd.DataFrame({"z": [3]}),
        },
    )

    rows = adb.audit_duplicates(only=("tbl",), verbose=False)
    assert rows[0]["pk_dup"] == -1, "主键列在全部文件中都不存在 → -1（旧语义）"
