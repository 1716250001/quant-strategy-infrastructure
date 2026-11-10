# -*- coding: utf-8 -*-
"""CC-FIX-16：`once` 断点语义 ＋ 调度护栏（估算器参数序／预算／刷新拒绝）。

判据对应：B1（参数序）／B2（est_error）／B3（once 语义）／B4（非 once 回归）／B5（预算）。
"""
import inspect

import fetch.backfill as backfill
import fetch.schedule_runner as sr
from config.backfill_targets import BACKFILL_TARGETS


class _Cp:
    """替身 Checkpoint：不碰真实断点文件。"""

    def __init__(self, *a, **kw):
        self.data, self.done = {}, set()

    def is_done(self, name, key):
        return (name, str(key)) in self.done

    def mark_done(self, name, key):
        self.done.add((name, str(key)))

    def mark_fetched(self, name, rows=None):
        self.data.setdefault(name, {})["last_fetch"] = "2026-11-10T12:00:00"

    def save(self):
        pass


# ---------------- B3：once 语义 ----------------

def test_build_tasks_once_sentinel_unchanged():
    tasks, kind = sr.build_tasks("cn_gdp", BACKFILL_TARGETS["cn_gdp"], dry_run=True)
    assert tasks == [None] and kind == "once"


def test_once_pending_survives_completed_marker():
    """T1：连续两跑 pending 仍非空（不会因 completed 被跳过）。"""
    cp = _Cp()
    for _ in range(2):
        pending, skipped = backfill._compute_pending(
            "cn_gdp", {"mode": "once"}, [None], "once", cp)
        assert pending == [None] and skipped == 0
        cp.mark_done("cn_gdp", "once")      # 跑完写标记，下次仍不得跳过


def test_once_not_skipped_even_if_sentinel_matches_marker():
    """T3 负例：若有人把哨兵改成 ["once"] 且 is_done 命中，仍不得被跳过。"""
    cp = _Cp()
    cp.mark_done("cn_gdp", "once")
    pending, skipped = backfill._compute_pending(
        "cn_gdp", {"mode": "once"}, ["once"], "once", cp)
    assert pending == ["once"] and skipped == 0


# ---------------- B4：非 once 回归 ----------------

def test_non_once_breakpoint_behavior_unchanged():
    """T2：range 型仍按断点跳过（行为零变化）。"""
    cp = _Cp()
    tasks = [("2010", "2011"), ("2011", "2012")]
    cp.mark_done("demo", "2010|2011")
    pending, skipped = backfill._compute_pending("demo", {}, tasks, "range", cp)
    assert pending == [("2011", "2012")] and skipped == 1


# ---------------- B1：参数序 ----------------

def test_est_requests_param_order(monkeypatch):
    """参数序锁定：必须是 (name, conf, tasks, kind, cp)，防「假绿恒 0」复发。"""
    params = list(inspect.signature(backfill._compute_pending).parameters)
    assert params[:5] == ["name", "conf", "tasks", "kind", "cp"]

    seen = {}

    def fake(name, conf, tasks, kind, cp, **kw):
        seen.update(name=name, conf=conf, kind=kind, tasks=tasks, cp=cp)
        return list(tasks), 0

    monkeypatch.setattr(sr, "_compute_pending", fake)
    monkeypatch.setattr(sr, "build_tasks", lambda n, c, dry_run=True: ([None], "once"))
    monkeypatch.setattr(sr, "estimate_requests", lambda tasks, conf: len(tasks))
    cp = _Cp()
    conf = {"mode": "once"}
    assert sr._est_requests("cn_gdp", conf, cp) == 1
    assert seen == {"name": "cn_gdp", "conf": conf, "kind": "once",
                    "tasks": [None], "cp": cp}


# ---------------- B2：估算失败不伪装 ----------------

def test_est_error_not_disguised_as_ok(monkeypatch):
    monkeypatch.setattr(sr, "build_tasks",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    rows = sr.select(only=["us_tradecal"], cp=_Cp())
    assert rows and rows[0][4] == "est_error"
    assert rows[0][3] == 0


# ---------------- B5：refresh 拒绝 / 预算 ----------------

def test_bare_refresh_rejected(monkeypatch):
    assert sr.run(refresh=True) == 1


def test_budget_exceeded_returns_2(monkeypatch):
    monkeypatch.setattr(sr, "Checkpoint", lambda *a, **kw: _Cp())
    monkeypatch.setattr(sr, "select",
                        lambda *a, **kw: [("t1", "M", True, 500, "ok"),
                                          ("t2", "M", True, 600, "ok")])
    assert sr.run(dry_run=True, budget=600) == 2
    assert sr.run(dry_run=True, budget=2000) == 0
