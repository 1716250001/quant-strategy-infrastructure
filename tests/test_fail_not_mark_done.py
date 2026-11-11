# -*- coding: utf-8 -*-
"""tests/test_fail_not_mark_done.py — B 型缺陷守卫（P3-8）

B 型 = `upsert_by_year` 对「某年份分区写失败」**不抛异常、只返回 fail 计数**
（见 `common/parquet_store.py`）。若调用方只包 try/except 而不检查返回值，
就会「落盘失败仍记断点」⇒ 断点 completed、磁盘缺年份、永不重拉
（2026-09-22 P0 的原始形态）。

本测试钉死两处直调点的守卫：
  ① `fetch/periodic_update.py::fill_missing_codes` → 分区 fail 进 failed、不 mark_done；
     返空仍 mark_done（A 型口径，本单不动）。
  ② `fetch/cb_download.py::run_cb_download.on_result` → 分区 fail 计入失败、不 mark_done；
     空返回仍 mark_done（本单不动）。

证明力：把两处改动回退成旧代码，本文件必红（旧代码在 fail 时仍 mark_done）。
"""
import pandas as pd

import fetch.base as fb
import fetch.cb_download as cb
import fetch.periodic_update as pu


# ============================================================
# ① periodic_update.fill_missing_codes（B-1）
# ============================================================
class _FakeCP:
    """只记录断点写入，不落盘。"""
    last = None

    def __init__(self, *a, **k):
        self.done = []
        _FakeCP.last = self

    def mark_done(self, name, code):
        self.done.append((name, code))

    def save(self):
        pass


def _run_fill(monkeypatch, df, ok=True, upsert_result=None):
    monkeypatch.setattr(pu, "missing_codes", lambda name, spec: (["600000.SH"], ""))
    monkeypatch.setattr(fb, "Checkpoint", _FakeCP)
    monkeypatch.setattr(fb, "ts_fetch", lambda *a, **k: (df, ok))
    monkeypatch.setattr(pu, "upsert_by_year",
                        lambda *a, **k: dict(upsert_result or {"fail": 0, "added": 1}))
    spec = {"date_col": "ann_date", "subset": ["ts_code", "ann_date"],
            "api": "dividend"}
    return pu.fill_missing_codes("dividend", spec, pro=None, limiter=None)


def test_b1_partition_fail_not_mark_done(monkeypatch):
    """分区写失败 ⇒ 计入 failed，且**不**记断点。"""
    df = pd.DataFrame({"ts_code": ["600000.SH"], "ann_date": ["20240101"]})
    _n_ok, n_rows, failed = _run_fill(
        monkeypatch, df, upsert_result={"fail": 1, "added": 0, "partitions": 1})

    assert failed == ["600000.SH"], "分区失败必须计入 failed（供上层重试/告警）"
    assert _FakeCP.last.done == [], "分区失败不得记断点（否则永不重拉）"
    assert n_rows == 0


def test_b1_empty_result_still_mark_done(monkeypatch):
    """返空 = 源端事实「该标的无事件」⇒ 仍记断点（本单不动）。"""
    _n_ok, _n_rows, failed = _run_fill(monkeypatch, pd.DataFrame())

    assert failed == []
    assert _FakeCP.last.done == [("dividend", "600000.SH")]


# ============================================================
# ② cb_download.run_cb_download.on_result（B-2）
# ============================================================
def _run_cb(monkeypatch, tmp_path, df, ok=True, upsert_result=None):
    basic = pd.DataFrame({"ts_code": ["123456.SH"]})
    monkeypatch.setattr(cb, "MARKET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cb, "META_DIR", str(tmp_path / "meta"))
    monkeypatch.setattr(cb, "CB_DAILY_DIR", str(tmp_path / "cb_daily"))
    monkeypatch.setattr(cb, "get_pro", lambda: object())
    monkeypatch.setattr(cb, "ts_call_with_retry", lambda *a, **k: basic)
    monkeypatch.setattr(cb, "upsert_by_year",
                        lambda *a, **k: dict(upsert_result or {"fail": 0, "added": 1}))
    import common.reader as reader
    monkeypatch.setattr(reader, "codes_from_store", lambda name: [])

    def _fake_parallel(pro, api, codes, limiter, fetch_fn=None, max_workers=None,
                       on_result=None, progress=None, progress_every=None,
                       verbose=False):
        on_result("123456.SH", df, ok)
        return {"ok": 0, "empty": 0, "fail": 0 if ok else 1}

    monkeypatch.setattr(cb, "fetch_codes_parallel", _fake_parallel)

    cb.run_cb_download(dry_run=False, workers=1)
    return cb.CBCheckpoint(str(tmp_path))


def test_b2_partition_fail_not_mark_done(monkeypatch, tmp_path):
    """cb_daily 某年份写失败 ⇒ 计入 fail，且**不**记断点。"""
    df = pd.DataFrame({"ts_code": ["123456.SH"], "trade_date": ["20240102"]})
    cp = _run_cb(monkeypatch, tmp_path,
                 df, upsert_result={"fail": 1, "added": 0, "partitions": 1})

    assert not cp.is_done("123456.SH"), "分区失败不得记断点（否则永不重拉）"


def test_b2_empty_result_still_mark_done(monkeypatch, tmp_path):
    """返空 = 该债无日线 ⇒ 仍记断点（本单不动）。"""
    cp = _run_cb(monkeypatch, tmp_path, pd.DataFrame())

    assert cp.is_done("123456.SH")
