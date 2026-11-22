# -*- coding: utf-8 -*-
"""D3 断点/写盘并发与原子性（REQ-20261121-06）

覆盖 03 路线图 §五 D3 的验收项：
  ① 10 个线程（各自 Checkpoint 实例）写同一断点文件不同表 → 键全部保留；
  ② os.replace 前两次 PermissionError → 第三次成功，文件完整；
  ③ monkeypatch upsert_by_year 抛 RuntimeError → save_df 上抛、不回退单文件；
  ④ 锁超时抛 LockTimeout，信息含持锁 PID，且不自动删残锁；
  ⑤ 旧 schema 兼容：无 status/partial 字段按 done，partial 合并去重；
  ⑥ upsert_by_year 写前确实持有目录级写锁。
"""

import json
import os
import threading

import pandas as pd
import pytest

import common.concurrency as cc
import common.parquet_store as ps
import fetch.backfill_io as bio
from common.concurrency import LockTimeout, file_lock
from fetch.base import Checkpoint

# 全部为 config/checkpoint_registry.py 已登记的表名（mark_done 会拒绝未登记表）
TABLES = [
    "daily",
    "daily_basic",
    "adj_factor",
    "stk_limit",
    "moneyflow",
    "index_daily",
    "balancesheet",
    "cashflow",
    "income",
    "fina_indicator",
]


# ============================================================
# ① 并发写同一断点：不丢键、不半截、不残留锁
# ============================================================
def test_concurrent_checkpoint_no_lost_keys(tmp_path):
    barrier = threading.Barrier(len(TABLES))
    errors = []

    def worker(i, table):
        try:
            barrier.wait(timeout=10)
            cp = Checkpoint(data_root=str(tmp_path))
            assert cp.mark_done(table, f"key{i}")
            cp.mark_fetched(table)
            cp.save()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i, t)) for i, t in enumerate(TABLES)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)

    assert not errors, f"并发 save 抛异常: {errors!r}"
    assert all(not th.is_alive() for th in threads)

    path = tmp_path / ".checkpoint.json"
    data = json.loads(path.read_text(encoding="utf-8"))  # 半截 JSON 会在此抛
    for i, table in enumerate(TABLES):
        assert f"key{i}" in data[table]["completed"], f"{table} 的键被覆盖丢失"
    assert data[table]["status"] == "done"

    assert not (tmp_path / ".checkpoint.json.lock").exists(), "锁文件未释放"
    assert not list(tmp_path.glob("*.tmp")), "残留 tmp 文件"


# ============================================================
# ② os.replace 瞬时冲突重试
# ============================================================
def test_replace_with_retry_twice_then_success(monkeypatch, tmp_path):
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "sharing violation", str(dst))
        real_replace(src, dst)

    monkeypatch.setattr(cc.os, "replace", flaky)
    src = tmp_path / "a.tmp"
    dst = tmp_path / "a.json"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")

    cc.replace_with_retry(str(src), str(dst), retries=5, retry_ms=1)

    assert calls["n"] == 3
    assert dst.read_text(encoding="utf-8") == "new"


def test_checkpoint_save_survives_transient_replace_error(monkeypatch, tmp_path):
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "sharing violation", str(dst))
        real_replace(src, dst)

    cp = Checkpoint(data_root=str(tmp_path))
    cp.mark_done("daily", "600519.SH")

    monkeypatch.setattr(cc.os, "replace", flaky)
    cp.save()

    assert calls["n"] == 3
    data = json.loads((tmp_path / ".checkpoint.json").read_text(encoding="utf-8"))
    assert data["daily"]["completed"] == ["600519.SH"]


# ============================================================
# ③ save_df fail-closed：按年分区失败不回退单文件
# ============================================================
def test_save_df_raises_and_no_single_file(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise RuntimeError("模拟磁盘写失败")

    monkeypatch.setattr(bio, "upsert_by_year", boom)
    target = tmp_path / "daily"
    df = pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20240102"]})

    with pytest.raises(RuntimeError, match="模拟磁盘写失败"):
        bio.save_df(str(target), "daily", df, layout="by_year")

    assert not (target / "daily.parquet").exists(), "不得回退写单文件"
    assert not list(target.glob("*.parquet"))


def test_save_df_value_error_also_propagates(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise ValueError("日期列全空")

    monkeypatch.setattr(bio, "upsert_by_year", boom)
    target = tmp_path / "cb_issue"
    df = pd.DataFrame({"ts_code": ["123456.SH"], "ann_date": [None]})

    with pytest.raises(ValueError):
        bio.save_df(str(target), "cb_issue", df, layout="by_year", date_col="ann_date")
    assert not list(target.glob("*.parquet"))


# ============================================================
# ④ 锁超时：信息含 PID、不自动删残锁
# ============================================================
def test_lock_timeout_reports_pid_and_keeps_file(tmp_path):
    lock_path = tmp_path / "held.lock"
    lock_path.write_text(f"{os.getpid()} 2026-11-21T10:00:00", encoding="utf-8")

    with pytest.raises(LockTimeout) as ei:
        file_lock(str(lock_path), timeout=0.05, retry_ms=5).acquire()

    msg = str(ei.value)
    assert str(os.getpid()) in msg, "超时信息必须含持锁 PID"
    assert str(lock_path) in msg
    assert "人工删除" in msg
    assert lock_path.exists(), "残锁不得自动删除"


def test_file_lock_released_on_exit(tmp_path):
    lock_path = str(tmp_path / "x.lock")
    with file_lock(lock_path, timeout=1):
        assert os.path.exists(lock_path)
        with pytest.raises(LockTimeout):
            file_lock(lock_path, timeout=0.02, retry_ms=5).acquire()
    assert not os.path.exists(lock_path)


# ============================================================
# ⑤ 旧 schema 兼容 + partial 合并
# ============================================================
def test_old_schema_status_defaults_done(tmp_path):
    path = tmp_path / ".checkpoint.json"
    path.write_text(json.dumps({"daily": {"completed": ["600519.SH"]}}), encoding="utf-8")

    cp = Checkpoint(data_root=str(tmp_path))
    assert cp.is_done("daily", "600519.SH")
    assert cp.get_status("daily") == "done"
    assert cp.is_partial("daily", "600519.SH") is False
    # 加载不得凭空写入新字段（旧文件原样可读）
    assert "status" not in cp.data["daily"]
    assert "partial" not in cp.data["daily"]


def test_partial_merge_union_and_not_completed(tmp_path):
    path = tmp_path / ".checkpoint.json"
    path.write_text(json.dumps({"daily": {"completed": ["600519.SH"]}}), encoding="utf-8")

    cp = Checkpoint(data_root=str(tmp_path))
    cp.mark_partial("daily", "000001.SZ")
    cp.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["daily"]["completed"] == ["600519.SH"]
    assert data["daily"]["partial"] == ["000001.SZ"]
    assert data["daily"]["status"] == "partial"

    # partial 键下次仍视为未完成；mark_done 后从 partial 移除
    cp2 = Checkpoint(data_root=str(tmp_path))
    assert cp2.is_partial("daily", "000001.SZ")
    cp2.mark_done("daily", "000001.SZ")
    assert not cp2.is_partial("daily", "000001.SZ")
    assert cp2.get_status("daily") == "done"


def test_merge_sums_counts_of_distinct_fetches(tmp_path):
    path = tmp_path / ".checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "daily": {
                    "completed": ["A"],
                    "last_fetch": "2026-11-21T10:00:00",
                    "last_fetch_rows": 5,
                }
            }
        ),
        encoding="utf-8",
    )

    cp = Checkpoint(data_root=str(tmp_path))
    cp.data["daily"]["last_fetch"] = "2026-11-21T11:00:00"
    cp.data["daily"]["last_fetch_rows"] = 7
    cp.save()

    data = json.loads(path.read_text(encoding="utf-8"))["daily"]
    assert data["last_fetch"] == "2026-11-21T11:00:00"  # 取较新
    assert data["last_fetch_rows"] == 12  # 两次拉取计数相加


def test_legacy_nondict_top_key_preserved(tmp_path):
    """`.backfill_checkpoint_codes.json` 顶层 'done'（list）不得被合并写丢掉。"""
    path = tmp_path / ".checkpoint.json"
    path.write_text(json.dumps({"done": ["legacy-marker"]}), encoding="utf-8")

    cp = Checkpoint(data_root=str(tmp_path))
    cp.mark_done("daily", "600519.SH")
    cp.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["done"] == ["legacy-marker"]
    assert data["daily"]["completed"] == ["600519.SH"]


# ============================================================
# ⑥ upsert_by_year 写前持目录级锁
# ============================================================
def test_upsert_by_year_holds_dir_write_lock(monkeypatch, tmp_path):
    target = tmp_path / "moneyflow"
    target.mkdir()
    (target / ".write.lock").write_text("999999 2026-11-21T10:00:00", encoding="utf-8")
    monkeypatch.setattr(ps, "_WRITE_LOCK_TIMEOUT", 0.05)

    df = pd.DataFrame({"trade_date": ["20240102"], "ts_code": ["600000.SH"]})
    with pytest.raises(LockTimeout):
        ps.upsert_by_year(df, str(target), date_col="trade_date", subset="*")

    assert not list(target.glob("*.parquet")), "未获锁不得写入任何分区"
