# -*- coding: utf-8 -*-
"""D4-B 完成语义与可观测测试（REQ-20261121-06）

覆盖：
  ① YearBatchWriter 回调携带 (done_keys, partial_keys)，partial 不记 done；
  ② by_param 分页触顶 → `mark_partial`，不 `mark_done`；
  ③ freshness：extend 组表按 daily 档；17 天停更判黄；
  ④ fingerprint `--report` 有差异 → rc=1，无差异 → 0；
  ⑤ 本地日历未覆盖 target → `CalendarStaleError`，日更返回 fail=1 且不打印「已是最新」。
"""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

import common.calendar as cal
import common.parquet_store as ps
import fetch.backfill as bf
import fetch.daily_update as du
import tools.db.fingerprint as fp
from fetch.base import Checkpoint


# ============================================================
# ① YearBatchWriter 回调契约
# ============================================================
def test_year_batch_writer_partial_callback(tmp_path):
    seen = []

    def on_flushed(done_keys, partial_keys):
        seen.append((list(done_keys), list(partial_keys)))

    w = ps.YearBatchWriter(
        str(tmp_path / "daily"),
        date_col="trade_date",
        subset=["ts_code", "trade_date"],
        on_flushed=on_flushed,
    )
    df = pd.DataFrame({"ts_code": ["A"], "trade_date": ["20240102"]})
    w.add(df, "20240102", partial=False)
    w.add(df, "20240103", partial=True)
    w.flush()

    assert seen == [(["20240102"], ["20240103"])]
    assert (tmp_path / "daily" / "2024.parquet").exists()


def test_year_batch_writer_partial_does_not_mark_done(tmp_path):
    cp = Checkpoint(data_root=str(tmp_path))

    def on_flushed(done_keys, partial_keys):
        for k in done_keys:
            cp.mark_done("daily", k)
        for k in partial_keys:
            cp.mark_partial("daily", k)

    w = ps.YearBatchWriter(
        str(tmp_path / "daily"),
        date_col="trade_date",
        subset=["ts_code", "trade_date"],
        on_flushed=on_flushed,
    )
    df = pd.DataFrame({"ts_code": ["A"], "trade_date": ["20240102"]})
    w.add(df, "20240102", partial=True)
    w.flush()
    cp.save()

    assert cp.is_partial("daily", "20240102")
    assert not cp.is_done("daily", "20240102")


def test_year_batch_writer_counts_write_fail(monkeypatch, tmp_path):
    """R1：落盘异常必须计数（供 run_target 汇总 fail），且不触发断点回调。"""

    def boom(*_a, **_k):
        raise RuntimeError("模拟落盘失败")

    monkeypatch.setattr(ps, "upsert_by_year", boom)
    seen = []
    w = ps.YearBatchWriter(
        str(tmp_path / "daily"),
        date_col="trade_date",
        subset="*",
        on_flushed=lambda d, p: seen.append((d, p)),
    )
    w.add(pd.DataFrame({"trade_date": ["20260102"], "ts_code": ["A"]}), "20260102")
    w.flush()

    assert w.write_fail == 1
    assert seen == [], "落盘失败不得触发 on_flushed（不得记断点）"


# ============================================================
# ② by_param 分页触顶 → partial
# ============================================================
def test_by_param_truncation_marks_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(bf, "BACKFILL_DIR_ROOT", str(tmp_path))
    page = pd.DataFrame(
        {
            "ts_code": ["A"] * 2000,
            "name": ["n"] * 2000,
            "exchange": ["SSE"] * 2000,
        }
    )
    monkeypatch.setattr(bf, "ts_call_with_retry", lambda *a, **k: page)

    cp = Checkpoint(data_root=str(tmp_path))
    conf = dict(bf.BACKFILL_TARGETS["fut_basic"])
    bf.run_target(
        object(), "fut_basic", conf, cp, workers=1, dry_run=False, ignore_checkpoint=True, limit=1
    )

    rec = cp.data.get("fut_basic", {})
    assert rec.get("partial"), "分页触顶必须进 partial"
    assert not rec.get("completed"), "partial 不得写 completed"


# ============================================================
# ③ freshness 分档
# ============================================================
def test_freshness_extend_tables_are_daily_tier():
    import tools.db.freshness as fr

    assert "moneyflow" in fr._DAILY_TABLES  # extend 组
    assert "daily" in fr._DAILY_TABLES  # core / EXTRA
    assert "dividend" not in fr._DAILY_TABLES  # 事件面周期表


def test_freshness_forecast_17days_is_yellow():
    import tools.db.freshness as fr

    assert "forecast" in fr._DAILY_TABLES
    assert fr._verdict(ds=17, dw=31, tier="daily") == "黄"


# ============================================================
# ③b 周期任务：_prepare 失败 → 只写 last_attempt，不写 last_run
# ============================================================
def test_periodic_prepare_failure_writes_last_attempt(monkeypatch, tmp_path):
    import fetch.periodic_update as pu

    saved = {}
    monkeypatch.setattr(pu, "load_state", lambda: {})
    monkeypatch.setattr(pu, "save_state", lambda s: saved.update(s))
    monkeypatch.setattr(pu, "state_path", lambda: str(tmp_path / "state.json"))
    monkeypatch.setattr(pu, "data_recency", lambda name, col: "20260102")
    monkeypatch.setattr(pu, "missing_codes", lambda name, spec: ([], ""))
    monkeypatch.setattr(pu, "pull_window", lambda spec, recency: ("20260101", "20260102"))
    monkeypatch.setattr(pu, "fetch_by_range", lambda *a, **k: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(pu, "_prepare", lambda df, spec: (None, 0, 0, "列缺失"))
    monkeypatch.setattr(pu, "get_pro", lambda: object())
    monkeypatch.setattr(
        pu, "RateLimiter", lambda **k: SimpleNamespace(total_requests=0, status_str=lambda: "")
    )

    res = pu.run_periodic_update(only=["dividend"], dry_run=False, force=True, quiet=True)
    rec = saved.get("dividend", {})
    assert "last_attempt" in rec
    assert "last_run" not in rec, "_prepare 失败不得写 last_run"
    assert rec.get("last_error") == "列缺失"
    assert res["dividend"]["ok"] is False


# ============================================================
# ④ fingerprint --report 差异返回 1
# ============================================================
def test_fingerprint_report_diff_rc1(tmp_path):
    root = tmp_path / "db"
    root.mkdir()
    (root / "a.txt").write_text("v1", encoding="utf-8")
    baseline = tmp_path / "base.json"

    assert fp.main(["--build", "--root", str(root), "--baseline", str(baseline)]) == 0
    assert fp.main(["--verify", "--report", "--root", str(root), "--baseline", str(baseline)]) == 0

    (root / "a.txt").write_text("v2-changed", encoding="utf-8")
    assert fp.main(["--verify", "--report", "--root", str(root), "--baseline", str(baseline)]) == 1


# ============================================================
# ⑤ 日历覆盖
# ============================================================
def test_calendar_coverage_raises(monkeypatch, tmp_path):
    p = tmp_path / "trade_cal.parquet"
    pd.DataFrame(
        {
            "exchange": ["SSE", "SSE"],
            "cal_date": ["20261119", "20261120"],
            "is_open": [1, 1],
        }
    ).to_parquet(p, index=False)
    monkeypatch.setattr(cal, "CAL_PATH", str(p))

    assert cal.check_coverage("20261120") == "20261120"
    with pytest.raises(cal.CalendarStaleError, match="未覆盖 target=20261121"):
        cal.check_coverage("20261121")


def test_daily_update_calendar_stale_returns_fail(monkeypatch, capsys):
    def boom(_target):
        raise cal.CalendarStaleError("本地日历未覆盖 target=20261121")

    monkeypatch.setattr(du, "get_latest_trade_date", lambda target=None: "20261121")
    monkeypatch.setattr(du, "check_coverage", boom)

    result = du.run_daily_update(
        target_date="20261121", dry_run=True, groups=("core",), with_periodic=False
    )
    out = capsys.readouterr().out
    assert result["fail"] == 1
    assert "本地日历未覆盖 target" in out
    assert "已是最新" not in out


# ============================================================
# ⑥ 派生脚本：失败保留已成功表，下次跳过已完成表
# ============================================================
def _load_derived_module():
    import importlib.util
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "赤潮" / "scripts" / "build_derived.py"
    spec = importlib.util.spec_from_file_location("build_derived_d4", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_derived_state_skip_and_failure(monkeypatch, tmp_path):
    mod = _load_derived_module()
    out = tmp_path / "derived"
    out.mkdir()
    monkeypatch.setattr(mod, "OUT", str(out))
    monkeypatch.setattr(mod, "STATE_PATH", str(out / "_state" / "build_state.json"))

    # 源端：a 有 20260101，b 有 20260101
    for t in ("a", "b"):
        d = tmp_path / "src" / t
        d.mkdir(parents=True)
        pd.DataFrame({"trade_date": ["20260101"]}).to_parquet(d / "2026.parquet", index=False)

    calls = []

    def ok():
        calls.append("a")

    def fail():
        calls.append("b")
        raise RuntimeError("模拟派生失败")

    monkeypatch.setattr(
        mod,
        "_TASKS",
        [
            ("a", "a", "trade_date", str(tmp_path / "src"), ok),
            ("b", "b", "trade_date", str(tmp_path / "src"), fail),
        ],
    )

    assert mod.main(["all"]) == 1  # 有表失败 → 非零
    log = json.loads((out / "_state" / "build_state.json").read_text(encoding="utf-8"))
    assert log["a"]["last_success"]
    assert "last_error" in log["b"]

    calls.clear()
    assert mod.main(["all"]) == 1  # b 仍失败
    assert calls == ["b"], "源端未前进的 a 应跳过，只补失败表 b"

    # 源端前进 → a 重新执行
    pd.DataFrame({"trade_date": ["20260102"]}).to_parquet(
        tmp_path / "src" / "a" / "2026.parquet", index=False
    )
    calls.clear()
    mod.main(["all"])
    assert "a" in calls
