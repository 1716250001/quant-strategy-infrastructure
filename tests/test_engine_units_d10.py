# -*- coding: utf-8 -*-
"""D10 引擎关键路径单测（REQ-20261121-06）

覆盖 03 §D10「单测清单（按优先级）」：
  ① fetch.base.ts_call_with_retry：成功 / 限频重试 / 权限错误 / None 返回；
  ② fetch.base.RateLimiter：每 API 独立预约 / 并发线程安全；
  ③ fetch.daily_update.resolve_gaps：by_date / by_range / 周月 / 空库 / 日历截断；
  ④ common.parquet_store.merge_append / upsert_by_year：新增 / 值修正 / 重复折叠 / schema 对齐；
  ⑤ common.reader：单码 / 区间 / 布局检测；
  ⑥ common.calendar：覆盖校验 / 陈旧日历；
  ⑦ alt.spec + alt.update：tier 派生 / rc 映射 / retired 表；
  ⑧ wind.catalog：路径派生（不触网、不写盘）。

纪律：全部用 tmp_path 造小 parquet / 小文件，不读生产库、不写 D:\\全量数据。
"""

import os
import threading
import time
from types import SimpleNamespace

import pandas as pd
import pyarrow.parquet as pq
import pytest

import alt.spec as sp_mod
import alt.update as up
import common.calendar as cal
import common.parquet_store as ps
import common.reader as reader
import fetch.base as fb
import fetch.daily_update as du
import wind.catalog as wc
import wind.config as wcfg


# ============================================================
# ① ts_call_with_retry
# ============================================================
class _Limiter:
    """无阻塞限频器（只记调用，不睡）。"""

    def __init__(self, allow=True):
        self.daily_limit = 10**9
        self.daily_counts = {}
        self.total_requests = 0
        self._allow = allow

    def wait(self, api_name=None):
        pass

    def check_daily(self, api_name):
        return self._allow

    def record(self, api_name):
        self.daily_counts[api_name] = self.daily_counts.get(api_name, 0) + 1
        self.total_requests += 1


def _no_sleep(monkeypatch):
    """把 fetch.base 的 time 换成「不睡」的假模块（不污染全局 time）。"""
    monkeypatch.setattr(fb, "time", SimpleNamespace(time=time.time, sleep=lambda _s: None))


def _pro(**methods):
    return SimpleNamespace(**methods)


def test_ts_call_success_returns_df():
    df = pd.DataFrame({"a": [1]})
    pro = _pro(daily=lambda **kw: df)
    out = fb.ts_call_with_retry(pro, "daily", _Limiter(), {}, verbose=False)
    assert out is df


def test_ts_call_none_returns_empty_df():
    pro = _pro(daily=lambda **kw: None)
    out = fb.ts_call_with_retry(pro, "daily", _Limiter(), {}, verbose=False)
    assert isinstance(out, pd.DataFrame) and out.empty


def test_ts_call_rate_limit_retries_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(fb, "TS_RATE_BACKOFF", [0.0])
    calls = {"n": 0}

    def flaky(**_kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("抱歉，您访问接口(daily)频率超限")
        return pd.DataFrame({"a": [1]})

    out = fb.ts_call_with_retry(_pro(daily=flaky), "daily", _Limiter(), {}, verbose=False)
    assert calls["n"] == 3 and len(out) == 1


def test_ts_call_permission_error_returns_none_without_retry(monkeypatch):
    fb.clear_permission_denied()
    calls = {"n": 0}

    def denied(**_kw):
        calls["n"] += 1
        raise RuntimeError("抱歉，您没有接口(daily)访问权限，权限详情访问：...")

    try:
        out = fb.ts_call_with_retry(_pro(daily=denied), "daily", _Limiter(), {}, verbose=False)
        assert out is None
        assert calls["n"] == 1, "权限错误是永久性的，不得重试"
        assert fb.permission_denied_count() == 1
        assert "daily" in fb.permission_denied_apis()
        assert fb.permission_denied_records()[0]["params"] == {}
    finally:
        fb.clear_permission_denied()


def test_ts_call_daily_quota_exhausted_returns_none():
    calls = {"n": 0}

    def fn(**_kw):
        calls["n"] += 1
        return pd.DataFrame({"a": [1]})

    out = fb.ts_call_with_retry(_pro(daily=fn), "daily", _Limiter(allow=False), {}, verbose=False)
    assert out is None and calls["n"] == 0


# ============================================================
# ② RateLimiter
# ============================================================
class _FakeTime:
    """带锁的假时钟：sleep 只推进时钟并记录秒数。"""

    def __init__(self):
        self.now = 1000.0
        self.slept = []
        self._lock = threading.Lock()

    def time(self):
        with self._lock:
            return self.now

    def sleep(self, seconds):
        with self._lock:
            self.slept.append(seconds)
            self.now += seconds


def test_rate_limiter_per_api_reservation(monkeypatch):
    fake = _FakeTime()
    monkeypatch.setattr(fb, "time", fake)
    monkeypatch.setattr(fb, "API_RATE_LIMIT", {"api_a": 60})
    monkeypatch.setattr(fb, "API_RATE_LIMIT_DEFAULT", 60000)

    rl = fb.RateLimiter()
    rl.wait("api_a")  # 首个槽位立即可用
    rl.wait("api_a")  # 第二个槽位需等 1s（60 次/分）
    rl.wait("api_b")  # 不同 API 独立桶，立即可用

    assert fake.slept == [1.0]
    assert rl.rate_summary() == {"api_a": 60, "api_b": 60000}


def test_rate_limiter_thread_safe(monkeypatch):
    interval = 0.005
    monkeypatch.setattr(fb, "API_RATE_LIMIT", {"fast": int(60 / interval)})
    monkeypatch.setattr(fb, "API_RATE_LIMIT_DEFAULT", int(60 / interval))

    rl = fb.RateLimiter()
    errors = []

    def worker():
        try:
            rl.wait("fast")
        except Exception as exc:  # pragma: no cover - 失败才走到
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    t0 = time.monotonic()
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    elapsed = time.monotonic() - t0

    assert not errors
    assert all(not th.is_alive() for th in threads)
    # 8 次预约 = 前 7 个间隔必须真实等待，否则多线程共享了同一槽位
    assert elapsed >= 7 * interval * 0.8
    assert elapsed < 5.0
    assert rl._buckets["fast"]["next_at"] - t0 >= 8 * interval - 0.02


# ============================================================
# ③ resolve_gaps
# ============================================================
DATES = ["20260105", "20260106", "20260107", "20260108", "20260109"]


def _patch_dates(monkeypatch, dates=DATES):
    monkeypatch.setattr(du.reader, "clear_cache", lambda *a, **k: None)
    monkeypatch.setattr(
        du, "open_dates_by_market", lambda market, start_date=None, end_date=None: list(dates)
    )


def test_resolve_gaps_by_date(monkeypatch):
    _patch_dates(monkeypatch)
    monkeypatch.setattr(du.reader, "latest_date", lambda *a, **k: "20260105")

    conf = {"mode": "by_date", "calendar": "SSE", "start": "20260101", "date_col": "trade_date"}
    dates, info = du.resolve_gaps("daily", conf, "20260109", 2)

    assert info["gap"] == ["20260106", "20260107", "20260108", "20260109"]
    assert info["refresh"] == ["20260108", "20260109"]
    assert set(dates) == set(info["gap"]) | set(info["refresh"])


def test_resolve_gaps_empty_store_uses_start(monkeypatch):
    _patch_dates(monkeypatch)
    monkeypatch.setattr(du.reader, "latest_date", lambda *a, **k: None)

    conf = {"mode": "by_date", "calendar": "SSE", "start": "20260107", "date_col": "trade_date"}
    dates, info = du.resolve_gaps("daily", conf, "20260109", 1)

    assert info["latest"] is None
    assert dates == ["20260107", "20260108", "20260109"]


def test_resolve_gaps_by_range(monkeypatch):
    _patch_dates(monkeypatch)
    monkeypatch.setattr(du.reader, "latest_date", lambda *a, **k: "20260105")

    conf = {"mode": "by_range", "date_col": "date"}
    dates, info = du.resolve_gaps("shibor", conf, "20260109", 2)
    assert dates == [("20260105", "20260109")]
    assert info["kind"] == "range"

    monkeypatch.setattr(du.reader, "latest_date", lambda *a, **k: "20260109")
    dates2, info2 = du.resolve_gaps("shibor", conf, "20260109", 2)
    assert dates2 == [] and info2["latest"] == "20260109"


def test_resolve_gaps_weekly_samples(monkeypatch):
    import fetch.backfill_spec as bspec

    monkeypatch.setattr(
        bspec,
        "week_last_trade_dates",
        lambda start, end, calendar="SSE", anchor="last_trade": [
            "20260102",
            "20260109",
            "20260116",
        ],
    )
    monkeypatch.setattr(du.reader, "clear_cache", lambda *a, **k: None)
    monkeypatch.setattr(du.reader, "latest_date", lambda *a, **k: "20260109")

    conf = {
        "mode": "by_date",
        "freq": "week",
        "start": "20260101",
        "calendar": "SSE",
        "date_col": "trade_date",
        "week_anchor": "friday",
    }
    dates, info = du.resolve_gaps("stk_weekly_monthly", conf, "20260116", 1)

    assert info["gap"] == ["20260116"]
    assert info["refresh"] == ["20260116"]
    assert dates == ["20260116"]


def test_resolve_gaps_monthly_empty_returns_all_samples(monkeypatch):
    import fetch.backfill_spec as bspec

    monkeypatch.setattr(
        bspec,
        "month_last_trade_dates",
        lambda start, end, calendar="SSE": [("2026-01", "20260130"), ("2026-02", "20260227")],
    )
    monkeypatch.setattr(du.reader, "clear_cache", lambda *a, **k: None)
    monkeypatch.setattr(du.reader, "latest_date", lambda *a, **k: None)

    conf = {
        "mode": "by_date",
        "freq": "month",
        "start": "20260101",
        "calendar": "SSE",
        "date_col": "trade_date",
    }
    dates, info = du.resolve_gaps("stk_monthly", conf, "20260227", 1)
    assert dates == ["20260130", "20260227"]
    assert info["gap"] == ["20260130", "20260227"]


def test_resolve_gaps_calendar_truncation_is_caught_by_coverage(monkeypatch, tmp_path):
    """日历截断时 resolve_gaps 返回空缺口 → 必须由 check_coverage 拦截。"""
    _patch_dates(monkeypatch, ["20260105", "20260106", "20260107"])
    monkeypatch.setattr(du.reader, "latest_date", lambda *a, **k: "20260107")

    conf = {"mode": "by_date", "calendar": "SSE", "start": "20260101", "date_col": "trade_date"}
    dates, info = du.resolve_gaps("daily", conf, "20260109", 0)
    assert info["gap"] == [] and dates == []  # ← 截断的"假已最新"

    p = tmp_path / "trade_cal.parquet"
    pd.DataFrame({"exchange": ["SSE"], "cal_date": ["20260107"], "is_open": [1]}).to_parquet(
        p, index=False
    )
    monkeypatch.setattr(cal, "CAL_PATH", str(p))
    with pytest.raises(cal.CalendarStaleError, match="未覆盖 target=20260109"):
        cal.check_coverage("20260109")


# ============================================================
# ④ parquet_store
# ============================================================
def test_merge_append_new_file(tmp_path):
    path = tmp_path / "2024.parquet"
    df = pd.DataFrame(
        {"trade_date": ["20240103", "20240102"], "ts_code": ["A", "A"], "close": [2.0, 1.0]}
    )
    changed, added = ps.merge_append(
        str(path), df, subset=["ts_code", "trade_date"], sort_by="trade_date"
    )
    assert changed and added == 2
    got = pd.read_parquet(path)
    assert got["trade_date"].tolist() == ["20240102", "20240103"]


def test_merge_append_value_correction(tmp_path):
    path = tmp_path / "2024.parquet"
    pd.DataFrame({"trade_date": ["20240102"], "ts_code": ["A"], "close": [1.0]}).to_parquet(
        path, index=False
    )

    new = pd.DataFrame({"trade_date": ["20240102"], "ts_code": ["A"], "close": [9.5]})
    changed, added = ps.merge_append(
        str(path), new, subset=["ts_code", "trade_date"], sort_by="trade_date"
    )
    assert changed and added == 0, "同键值修正应 changed=True / added=0"
    assert pd.read_parquet(path)["close"].tolist() == [9.5]


def test_merge_append_folds_existing_duplicates(tmp_path, capsys):
    path = tmp_path / "2024.parquet"
    dup = pd.DataFrame(
        {"trade_date": ["20240102", "20240102"], "ts_code": ["A", "A"], "close": [1.0, 1.0]}
    )
    dup.to_parquet(path, index=False)

    new = pd.DataFrame({"trade_date": ["20240103"], "ts_code": ["A"], "close": [2.0]})
    changed, added = ps.merge_append(
        str(path), new, subset=["ts_code", "trade_date"], sort_by="trade_date"
    )
    assert changed and added == 1
    assert len(pd.read_parquet(path)) == 2
    assert "重复已按" in capsys.readouterr().out


def test_merge_append_schema_alignment(tmp_path):
    path = tmp_path / "x.parquet"
    pd.DataFrame({"b": [1.0], "a": [2.0]}).to_parquet(path, index=False)
    schema_before = pq.read_schema(path)

    new = pd.DataFrame({"a": [3.0], "b": [4.0]})
    ps.merge_append(str(path), new, subset="*")
    schema_after = pq.read_schema(path)

    assert schema_after.names == schema_before.names == ["b", "a"]
    assert schema_after.types == schema_before.types
    assert len(pd.read_parquet(path)) == 2


def test_upsert_by_year_partitions_and_added(tmp_path):
    root = tmp_path / "daily"
    df = pd.DataFrame(
        {"trade_date": ["20231229", "20240102"], "ts_code": ["A", "A"], "close": [1.0, 2.0]}
    )
    r = ps.upsert_by_year(df, str(root), date_col="trade_date", subset=["ts_code", "trade_date"])
    assert r["partitions"] == 2 and r["added"] == 2
    assert (root / "2023.parquet").exists() and (root / "2024.parquet").exists()

    again = ps.upsert_by_year(
        df, str(root), date_col="trade_date", subset=["ts_code", "trade_date"]
    )
    assert again["added"] == 0


def test_upsert_by_year_fail_closed_on_bad_date(tmp_path):
    root = tmp_path / "daily"
    df = pd.DataFrame({"ts_code": ["A"]})
    with pytest.raises(KeyError):
        ps.upsert_by_year(df, str(root), date_col="trade_date")

    df2 = pd.DataFrame({"trade_date": [None], "ts_code": ["A"]})
    with pytest.raises(ValueError, match="拒绝静默丢弃"):
        ps.upsert_by_year(df2, str(root), date_col="trade_date")


# ============================================================
# ⑤ reader
# ============================================================
def _write_table(root, table, filename, df):
    d = root / table
    d.mkdir(parents=True, exist_ok=True)
    df.to_parquet(d / filename, index=False)


def test_reader_by_year_read_code_and_range(tmp_path):
    root = tmp_path / "db"
    _write_table(
        root,
        "daily",
        "2024.parquet",
        pd.DataFrame(
            {
                "ts_code": ["A", "A", "B"],
                "trade_date": ["20240102", "20240103", "20240102"],
                "close": [1.0, 2.0, 3.0],
            }
        ),
    )
    _write_table(
        root,
        "daily",
        "2025.parquet",
        pd.DataFrame({"ts_code": ["A"], "trade_date": ["20250102"], "close": [5.0]}),
    )
    reader.clear_cache("daily", root=str(root))

    assert reader.detect_layout("daily", root=str(root)) == "by_year"
    one = reader.read_code("daily", "A", start_date="20240103", end_date="20241231", root=str(root))
    assert one["trade_date"].tolist() == ["20240103"]
    rng = reader.read_range("daily", start_date="20250101", end_date="20251231", root=str(root))
    assert rng["close"].tolist() == [5.0]
    assert reader.latest_date("daily", root=str(root)) == "20250102"


def test_reader_by_code_layout(tmp_path):
    root = tmp_path / "db"
    _write_table(
        root,
        "fund_daily",
        "600000.SH.parquet",
        pd.DataFrame(
            {
                "ts_code": ["600000.SH", "600000.SH"],
                "trade_date": ["20240102", "20240103"],
                "close": [1.0, 2.0],
            }
        ),
    )
    reader.clear_cache("fund_daily", root=str(root))

    assert reader.detect_layout("fund_daily", root=str(root)) == "by_code"
    assert reader.list_codes("fund_daily", root=str(root)) == ["600000.SH"]
    got = reader.read_code("fund_daily", "600000.SH", start_date="20240103", root=str(root))
    assert got["close"].tolist() == [2.0]


def test_reader_single_layout(tmp_path):
    root = tmp_path / "db"
    _write_table(
        root,
        "margin",
        "margin.parquet",
        pd.DataFrame(
            {"ts_code": ["A", "B"], "trade_date": ["20240102", "20240102"], "rzye": [1.0, 2.0]}
        ),
    )
    reader.clear_cache("margin", root=str(root))

    assert reader.detect_layout("margin", root=str(root)) == "single"
    got = reader.read_code("margin", "B", root=str(root))
    assert got["rzye"].tolist() == [2.0]
    assert reader.detect_layout("no_such_table", root=str(root)) == "none"


# ============================================================
# ⑥ calendar
# ============================================================
def test_calendar_coverage_pass_and_stale(monkeypatch, tmp_path):
    p = tmp_path / "trade_cal.parquet"
    pd.DataFrame(
        {
            "exchange": ["SSE", "SSE", "SSE"],
            "cal_date": ["20260105", "20260106", "20260107"],
            "is_open": [1, 1, 0],
        }
    ).to_parquet(p, index=False)
    monkeypatch.setattr(cal, "CAL_PATH", str(p))

    assert cal.check_coverage("20260107") == "20260107"
    with pytest.raises(cal.CalendarStaleError, match="最大 20260107"):
        cal.check_coverage("20260108")
    assert cal.open_dates(exchange="SSE") == ["20260105", "20260106"]


def test_calendar_empty_raises(monkeypatch, tmp_path):
    p = tmp_path / "trade_cal.parquet"
    pd.DataFrame({"exchange": [], "cal_date": [], "is_open": []}).to_parquet(p, index=False)
    monkeypatch.setattr(cal, "CAL_PATH", str(p))
    with pytest.raises(cal.CalendarStaleError, match="本地日历为空"):
        cal.check_coverage("20260105")


# ============================================================
# ⑦ alt.spec / alt.update
# ============================================================
def test_alt_tiers_derived_from_specs():
    assert sp_mod.TIERS == list(sp_mod.BY_TIER)
    total = 0
    for tier in sp_mod.TIERS:
        specs = sp_mod.tier_specs(tier)
        assert [s.name for s in specs] == [s.name for s in sp_mod.BY_TIER[tier]]
        assert len({s.name for s in specs}) == len(specs)
        total += len(specs)
    assert total == len(sp_mod.ALL_SPECS)


def test_alt_retired_tables_age_720():
    retired = sp_mod._RETIRED_AGE_720
    assert len(retired) == 14
    for name in retired:
        assert sp_mod.get(name).max_age_days == 720


def test_alt_freshness_marks_retired(monkeypatch):
    monkeypatch.setattr(up, "table_dir_exists", lambda name: True)
    monkeypatch.setattr(up, "AltCheckpoint", lambda *a, **k: SimpleNamespace(data={}))
    monkeypatch.setattr(up, "_read_all", lambda sp: pd.DataFrame({"date": ["20240101"]}))

    rows = up.freshness_report([sp_mod.get("macro_usa_cpi")], verbose=False)
    assert rows[0]["state"] == "已登记停更"


def test_alt_update_rc_mapping(monkeypatch):
    monkeypatch.setattr(
        up, "run", lambda **kw: [{"table": "x", "action": "dates", "ok": False, "errors": ["boom"]}]
    )
    assert up.main(["--dry-run"]) == 1

    monkeypatch.setattr(
        up, "run", lambda **kw: [{"table": "x", "action": "skip", "ok": True, "errors": []}]
    )
    assert up.main(["--dry-run"]) == 0


# ============================================================
# ⑧ wind.catalog 路径派生
# ============================================================
def test_wind_catalog_path_derivation():
    assert wc.SERIES_DIR == os.path.join(wcfg.WIND_SERIES_DIR, "wind_index_daily")
    assert wc.RAW_DIR == wcfg.WIND_RAW_DIR
    assert os.path.dirname(wcfg.WIND_CATALOG_FILE) == wcfg.WIND_CATALOG_DIR


def test_wind_catalog_code_and_schema_helpers():
    assert wc._windcode_from_json_path(r"C:\x\raw\exp_8882563.json") == "8882563"
    assert wc._windcode_from_json_path(r"C:\x\raw\000300.SH.json") == "000300.SH"
    assert wc._windcode_from_parquet_path("/x/series/000300.SH.parquet") == "000300.SH"
    assert wc._schema_hash(["B", "A"]) == wc._schema_hash(["A", "B"])
    assert len(wc._schema_hash(["A"])) == 12
