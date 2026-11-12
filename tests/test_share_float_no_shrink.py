# -*- coding: utf-8 -*-
"""tests/test_share_float_no_shrink.py — share_float 折叠反例固化（CC-FIX-5R Step5）

背景（§362／§365）：
  旧配置用 4 键 subset=["ts_code","float_date","ann_date","holder_name"]
  判重，而源端存在「同码／同公告日／同解禁日／同持有人／同类型，仅
  解禁数量不同」的多笔记录 ⇒ 键式判重会静默折叠丢行（CC-FIX-4 实证
  丢失 53 行，CC-FIX-5 修复为全列判重 subset="*"）。

本测试把「为什么会丢」钉死成可执行反例：
  ① subset="*" 连写两次 → 仍 3 行（不折叠）
  ② 同数据换旧 4 键两次 → 变 2 行（复现折叠丢行）
  ③ 三处配置断言，防止回退
"""
import pandas as pd

from common.parquet_store import upsert_by_year
from config import BACKFILL_TARGETS, DB_AUDIT_PKEYS, PERIODIC_UPDATE_TARGETS

COLS = ["ts_code", "ann_date", "float_date", "float_share",
        "float_ratio", "holder_name", "share_type"]

OLD_4KEYS = ["ts_code", "float_date", "ann_date", "holder_name"]


def _sample():
    """3 行：前两行仅 float_share 不同（旧 4 键会折叠），第三行换持有人。"""
    return pd.DataFrame([
        ["000001.SZ", "20240101", "20240201", 400000.0, 0.12, "甲", "定增股份"],
        ["000001.SZ", "20240101", "20240201", 800000.0, 0.24, "甲", "定增股份"],
        ["000001.SZ", "20240101", "20240201", 300000.0, 0.09, "乙", "定增股份"],
    ], columns=COLS)


def _rows(tmp_path, year="2024"):
    return len(pd.read_parquet(tmp_path / f"{year}.parquet"))


def test_full_column_subset_keeps_all_rows(tmp_path):
    """① subset="*" 连写两次 → 仍 3 行"""
    df = _sample()
    upsert_by_year(df, str(tmp_path), date_col="ann_date", subset="*",
                   sort_by="ann_date")
    assert _rows(tmp_path) == 3, "全列判重首写后应保留 3 行"

    upsert_by_year(df, str(tmp_path), date_col="ann_date", subset="*",
                   sort_by="ann_date")
    assert _rows(tmp_path) == 3, "全列判重重复写不应折叠"

    got = pd.read_parquet(tmp_path / "2024.parquet")
    assert sorted(got["float_share"].tolist()) == [300000.0, 400000.0, 800000.0]


def test_old_four_key_subset_collapses_rows(tmp_path):
    """② 旧 4 键 subset 连写两次 → 折叠为 2 行（可执行反例）

    注意：首写是新建文件分支（原样落盘，3 行），折叠发生在**第二次写**
    —— 这正是日更/周期刷新重写年度文件时静默丢行的真实路径。
    """
    df = _sample()
    upsert_by_year(df, str(tmp_path), date_col="ann_date", subset=OLD_4KEYS,
                   sort_by="ann_date")
    assert _rows(tmp_path) == 3, "首写为新建文件分支，原样落盘 3 行"

    upsert_by_year(df, str(tmp_path), date_col="ann_date", subset=OLD_4KEYS,
                   sort_by="ann_date")
    assert _rows(tmp_path) == 2, "旧 4 键在重写时把同持有人两笔折叠成 1 行"


def test_share_float_config_never_regresses():
    """③ 三处配置断言：subset 全列判重、审计主键 None"""
    assert BACKFILL_TARGETS["share_float"]["subset"] == "*"
    assert PERIODIC_UPDATE_TARGETS["share_float"]["subset"] == "*"
    assert DB_AUDIT_PKEYS["share_float"] is None
