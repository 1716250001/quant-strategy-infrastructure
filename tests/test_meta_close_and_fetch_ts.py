# -*- coding: utf-8 -*-
"""CC-FIX-7（§362）：mark_fetched 行数 ＋ meta-close 纯函数 ＋ 补表重建口径。"""
import json

import pandas as pd
import pytest

from fetch.base import Checkpoint
from tools.db.meta_close import (
    coverage_missing,
    e_codes,
    rebuild_patch,
    status_nan_set,
)


# ============================================================
# ① mark_fetched(rows=5) 写 last_fetch_rows；旧文件无该键照常读
# ============================================================
def test_mark_fetched_rows_written(tmp_path):
    cp = Checkpoint(data_root=str(tmp_path))
    cp.mark_done("daily", "600519.SH")
    cp.mark_fetched("daily", rows=5)
    cp.save()

    d = json.loads((tmp_path / ".checkpoint.json").read_text(encoding="utf-8"))
    assert d["daily"]["completed"] == ["600519.SH"]
    assert "T" in d["daily"]["last_fetch"]
    assert d["daily"]["last_fetch_rows"] == 5


def test_old_checkpoint_without_rows_key(tmp_path):
    """旧断点（无 last_fetch/last_fetch_rows）加载不报错，is_done 行为不变。"""
    p = tmp_path / ".checkpoint.json"
    p.write_text(json.dumps({"daily": {"completed": ["600519.SH"]}}),
                 encoding="utf-8")
    cp = Checkpoint(data_root=str(tmp_path))
    assert cp.is_done("daily", "600519.SH")
    assert not cp.is_done("daily", "000001.SZ")
    assert cp.get_completed_count("daily") == 1
    assert "last_fetch" not in cp.data["daily"]


# ============================================================
# ② 失败路径：调用返回 None ⇒ 该表断点无 last_fetch
# ============================================================
def test_failed_call_does_not_write_last_fetch(tmp_path, monkeypatch):
    """函数粒度：by_range 分支，ts_call_with_retry 返回 None ⇒ 不写 last_fetch。"""
    import fetch.backfill as bf

    monkeypatch.setattr(bf, "ts_call_with_retry", lambda *a, **k: None)
    cp = Checkpoint(data_root=str(tmp_path),
                    filename=".backfill_checkpoint.json")
    conf = {"api": "fake_api_no_such", "mode": "by_range",
            "start": "20260101", "end": "20261231", "max_rows": 0}
    bf.run_target(object(), "fake_api_no_such", conf, cp,
                  workers=1, dry_run=False, verbose=False, limit=1)

    saved = json.loads((tmp_path / ".backfill_checkpoint.json")
                       .read_text(encoding="utf-8"))
    assert "last_fetch" not in saved.get("fake_api_no_such", {})
    assert "last_fetch_rows" not in saved.get("fake_api_no_such", {})


# ============================================================
# ③ meta-close 纯函数：E 计数 / status_nan 集合 / 未覆盖清单
# ============================================================
@pytest.fixture()
def sample_basic():
    return pd.DataFrame({
        "ts_code": ["A.SZ", "B.SZ", "C.SH", "D.SZ", "E.SZ"],
        "market":  ["E", "E", "E", "O", "E"],
        "name":    ["a", "b", "c", "d", "e"],
        "fund_type": ["股票型"] * 5,
        "list_date": ["20200101", "20200101", "20200101", "20200101", "20200101"],
        "status":  ["L", None, "D", "L", ""],
    })


def test_meta_close_pure_functions(sample_basic):
    assert e_codes(sample_basic) == ["A.SZ", "B.SZ", "C.SH", "E.SZ"]
    assert status_nan_set(sample_basic) == ["B.SZ", "E.SZ"]

    stores = {
        "fund_daily": {"A.SZ"},
        "fund_nav": set(),
        "fund_adj": {"A.SZ", "B.SZ"},
        "fund_share": {"A.SZ", "B.SZ", "C.SH", "E.SZ"},
    }
    registered = {"fund_daily": {"B.SZ"}, "fund_nav": set(),
                  "fund_adj": set(), "fund_share": set()}
    miss = coverage_missing(e_codes(sample_basic), stores, registered)
    # fund_daily：E 码 A/B/C/E，库内 A、已登记 B ⇒ 只剩 C/E
    assert miss["fund_daily"] == ["C.SH", "E.SZ"]
    # fund_nav：全空、无登记 ⇒ 全部 4 只
    assert miss["fund_nav"] == ["A.SZ", "B.SZ", "C.SH", "E.SZ"]
    # fund_adj：库内 A/B ⇒ 剩 C/E
    assert miss["fund_adj"] == ["C.SH", "E.SZ"]
    # fund_share：全覆盖 ⇒ 无缺口
    assert miss["fund_share"] == []


# ============================================================
# ④ 补表重建：no_daily / early / normal(ok) 三码
# ============================================================
def test_rebuild_patch_three_cases():
    basic = pd.DataFrame({
        "ts_code": ["NO.SZ", "EARLY.SZ", "OK.SH", "LATE.SH"],
        "market": ["E"] * 4,
        "name": ["n", "e", "o", "l"],
        "fund_type": ["股票型"] * 4,
        "list_date": ["20240101", "20240101", "20240101", "20240101"],
        "status": ["L", "L", "D", "L"],
    })
    first = {
        "EARLY.SZ": "20230101",   # 首笔行情早于上市 365 天
        "OK.SH": "20240101",      # 同日上市/首笔
        "LATE.SH": "20240315",    # 上市后 74 天才首笔 ⇒ late（>30 天）
    }
    out = rebuild_patch(basic, first).set_index("ts_code")

    assert out.loc["NO.SZ", "category"] == "no_daily"
    assert pd.isna(out.loc["NO.SZ", "early_days"])

    assert out.loc["EARLY.SZ", "category"] == "early"
    assert out.loc["EARLY.SZ", "early_days"] == 365.0

    assert out.loc["OK.SH", "category"] == "ok"
    assert out.loc["OK.SH", "early_days"] == 0.0

    assert out.loc["LATE.SH", "category"] == "late"
    assert out.loc["LATE.SH", "early_days"] == -74.0

    assert int((out["early_days"] > 0).sum()) == 1
    assert list(out.columns) == ["name", "fund_type", "list_date",
                                 "first_trade_date", "early_days",
                                 "category", "status"]
