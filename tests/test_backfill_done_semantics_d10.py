# -*- coding: utf-8 -*-
"""D10 执行链语义测试（REQ-20261121-06）

覆盖：
  ① D3 O3：`save_df` 抛 RuntimeError → `fetch/backfill.py` 的 on_result 不 `mark_done`
     （真实调用链：run_target → fetch_codes_parallel → on_result → save_df）；
  ② G1 R3：`by_param` 分页 `pages>1` 中途 `df is None` → n_fail 增计且不 `mark_done`；
  ③ G1 计数口径：`by_period` / `by_range` / `paged` 的 `done` = 实际成功数；
  ④ G1 O1 残量：download/cb 真实函数 + 桩源端的失败传播（替换 CLI 级 monkeypatch）。
"""

import time
from types import SimpleNamespace

import pandas as pd

import common.reader as reader
import fetch.backfill as bf
import fetch.base as fb
import fetch.cb_download as cb
import fetch.full_download as fd
from fetch.base import Checkpoint


class _Limiter:
    """无阻塞限频器。"""

    def __init__(self, *_a, **_k):
        self.daily_limit = 10**9
        self.daily_counts = {}
        self.total_requests = 0

    def wait(self, api_name=None):
        pass

    def check_daily(self, api_name):
        return True

    def record(self, api_name):
        self.daily_counts[api_name] = self.daily_counts.get(api_name, 0) + 1
        self.total_requests += 1

    def _rate_for(self, api_name):
        return 170

    def status_str(self):
        return "fake"


class _CP:
    """只记录断点写入的假断点对象（避免依赖 registry）。"""

    def __init__(self):
        self.done = []
        self.partial = []
        self.fail = 0
        self.fetches = 0
        self.saves = 0

    def is_done(self, name, key, **_kw):
        return False

    def is_partial(self, name, key, **_kw):
        return False

    def mark_done(self, name, key, **_kw):
        self.done.append((name, key))
        return True

    def mark_partial(self, name, key, **_kw):
        self.partial.append((name, key))
        return True

    def mark_failed(self, name, **_kw):
        self.fail += 1
        return True

    def mark_fetched(self, *_a, **_k):
        self.fetches += 1

    def save(self):
        self.saves += 1


def _patch_no_sleep(monkeypatch):
    monkeypatch.setattr(fb, "time", SimpleNamespace(time=time.time, sleep=lambda _s: None))


# ============================================================
# ① D3 O3：save_df RuntimeError → 不 mark_done（真实调用链）
# ============================================================
def test_o3_save_df_runtime_error_blocks_mark_done(monkeypatch, tmp_path):
    _patch_no_sleep(monkeypatch)
    monkeypatch.setattr(bf, "BACKFILL_DIR_ROOT", str(tmp_path))
    monkeypatch.setattr(
        bf, "build_tasks", lambda name, conf, dry_run=False: (["600000.SH"], "code")
    )
    monkeypatch.setattr(reader, "codes_from_store", lambda *a, **k: [])

    def boom(*_a, **_k):
        raise RuntimeError("模拟磁盘写失败")

    monkeypatch.setattr(bf, "save_df", boom)

    df = pd.DataFrame(
        {"ts_code": ["600000.SH"], "end_date": ["20241231"], "ann_date": ["20240101"]}
    )
    pro = SimpleNamespace(dividend=lambda **_kw: df)
    cp = Checkpoint(data_root=str(tmp_path))
    conf = dict(bf.BACKFILL_TARGETS["dividend"])

    r = bf.run_target(pro, "dividend", conf, cp, workers=1, dry_run=False)

    assert r["fail"] >= 1, "落盘失败必须上抛为 fail"
    assert not cp.is_done("dividend", "600000.SH"), (
        "save_df 抛异常后不得 mark_done（否则该标的数据永不重拉）"
    )
    assert not list((tmp_path / "dividend").glob("*.parquet"))


# ============================================================
# ② G1 R3：by_param 分页中途 None
# ============================================================
def test_r3_by_param_midpage_none_no_mark_done(monkeypatch, tmp_path):
    monkeypatch.setattr(bf, "BACKFILL_DIR_ROOT", str(tmp_path))
    monkeypatch.setattr(bf, "build_tasks", lambda *_a, **_k: (["SHFE"], "param"))
    page = pd.DataFrame({"ts_code": ["A", "B"], "name": ["a", "b"], "exchange": ["SHFE", "SHFE"]})
    calls = {"n": 0}

    def fake_retry(*_a, **_k):
        calls["n"] += 1
        return page if calls["n"] == 1 else None

    monkeypatch.setattr(bf, "ts_call_with_retry", fake_retry)

    cp = _CP()
    conf = dict(bf.BACKFILL_TARGETS["fut_basic"])
    conf.update({"mode": "by_param", "param_name": "exchange", "page_size": 2, "max_pages": 5})

    r = bf.run_target(object(), "fut_basic", conf, cp, workers=1, dry_run=False)

    assert calls["n"] == 2
    assert r["fail"] >= 1, "分页中途失败必须计入 fail"
    assert cp.done == [], "分页中途失败不得 mark_done"
    assert not list((tmp_path / "fut_basic").glob("*.parquet")), (
        "中途失败不落盘（避免把不完整页固化成完成）"
    )


# ============================================================
# ③ 计数口径：done = 实际成功数
# ============================================================
def test_by_period_done_counts_success_only(monkeypatch, tmp_path):
    monkeypatch.setattr(bf, "BACKFILL_DIR_ROOT", str(tmp_path))
    monkeypatch.setattr(bf, "build_tasks", lambda *_a, **_k: (["20251231", "20250630"], "period"))
    df = pd.DataFrame({"ts_code": ["A"], "end_date": ["20251231"], "ann_date": ["20260120"]})
    seq = [df, None]
    monkeypatch.setattr(bf, "ts_call_with_retry", lambda *_a, **_k: seq.pop(0))

    cp = _CP()
    conf = {
        "mode": "by_period",
        "api": "disclosure_date",
        "period_param": "end_date",
        "date_col": "end_date",
        "cap": 0,
        "subset": ["ts_code", "end_date"],
    }
    r = bf.run_target(object(), "disclosure_date", conf, cp, workers=1, dry_run=False)

    assert r["done"] == 1, "两个报告期只成功一个，done 必须为 1"
    assert r["fail"] == 1


def test_by_range_done_counts_success_only(monkeypatch, tmp_path):
    monkeypatch.setattr(bf, "BACKFILL_DIR_ROOT", str(tmp_path))
    monkeypatch.setattr(
        bf,
        "build_tasks",
        lambda *_a, **_k: ([("20260101", "20260131"), ("20260201", "20260228")], "range"),
    )
    df = pd.DataFrame({"trade_date": ["20260102"], "value": [1.0]})
    seq = [df, None]
    monkeypatch.setattr(bf, "ts_call_with_retry", lambda *_a, **_k: seq.pop(0))

    cp = _CP()
    conf = {"mode": "by_range", "api": "moneyflow_hsgt", "subset": "*", "max_rows": 0}
    r = bf.run_target(object(), "moneyflow_hsgt", conf, cp, workers=1, dry_run=False)

    assert r["done"] == 1
    assert r["fail"] == 1


def test_paged_done_counts_success_only(monkeypatch, tmp_path):
    monkeypatch.setattr(bf, "BACKFILL_DIR_ROOT", str(tmp_path))
    monkeypatch.setattr(bf, "build_tasks", lambda *_a, **_k: ([0, 1], "paged"))
    page = pd.DataFrame({"ts_code": ["A", "B"], "symbol": ["a", "b"]})
    seq = [page, None]
    monkeypatch.setattr(bf, "ts_call_with_retry", lambda *_a, **_k: seq.pop(0))

    cp = _CP()
    conf = {"mode": "paged", "api": "us_basic", "page_size": 2, "subset": "*"}
    r = bf.run_target(object(), "us_basic", conf, cp, workers=1, dry_run=False)

    assert r["done"] == 1, "两页只成功一页，done 必须为 1"
    assert r["fail"] == 1


# ============================================================
# ④ G1 O1 残量：download/cb 真实函数 + 桩源端
# ============================================================
def test_o1_cb_real_function_propagates_source_failure(monkeypatch, tmp_path):
    _patch_no_sleep(monkeypatch)
    basic = pd.DataFrame({"ts_code": ["123456.SH"]})

    class _Pro:
        def cb_basic(self, **_kw):
            return basic

        def cb_daily(self, **_kw):
            raise RuntimeError("源端不可用")

    monkeypatch.setattr(cb, "get_pro", lambda: _Pro())
    monkeypatch.setattr(cb, "MARKET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cb, "META_DIR", str(tmp_path / "meta"))
    monkeypatch.setattr(cb, "CB_DAILY_DIR", str(tmp_path / "cb_daily"))
    monkeypatch.setattr(cb, "RateLimiter", _Limiter)
    monkeypatch.setattr(reader, "codes_from_store", lambda *a, **k: [])

    res = cb.run_cb_download(dry_run=False, workers=1)

    assert res["fail"] == 1 and res["ok"] == 0, "取数失败必须计入 fail（不得折成「空」）"
    cp = cb.CBCheckpoint(str(tmp_path))
    assert not cp.is_done("123456.SH"), "取数失败不得 mark_done"


def test_o1_download_full_real_function_propagates_source_failure(monkeypatch, tmp_path):
    _patch_no_sleep(monkeypatch)

    class _Pro:
        def __getattr__(self, name):
            def _boom(**_kw):
                raise RuntimeError("源端不可用")

            return _boom

    stock = pd.DataFrame({"ts_code": ["600000.SH"]})
    monkeypatch.setattr(fd, "get_pro", lambda: _Pro())
    monkeypatch.setattr(fd, "MARKET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(fd, "META_DIR", str(tmp_path / "metadata"))
    monkeypatch.setattr(fd, "RateLimiter", _Limiter)
    monkeypatch.setattr(
        fd, "_load_local_metadata", lambda *a, **k: (stock, pd.DataFrame({"ts_code": []}))
    )
    monkeypatch.setattr(reader, "codes_from_store", lambda *a, **k: [])

    res = fd.run_download(phase="daily", workers=1)

    assert res["fail"] >= 1
    assert res["apis"].get("daily") == 1, "daily 取数失败必须传播到 run_download"
