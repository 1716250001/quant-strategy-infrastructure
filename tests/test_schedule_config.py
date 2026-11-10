# -*- coding: utf-8 -*-
"""CC-FIX-13（R3'-b）：调度频率表覆盖率与到期判定。"""
from datetime import date, timedelta

import pytest

from config.backfill_targets import BACKFILL_TARGETS
from config.daily import (DAILY_UPDATE_EXTRA, DAILY_UPDATE_GROUPS,
                          PERIODIC_UPDATE_TARGETS)
from config.schedule import EXCLUDED, FREQ_DAYS, SCHEDULE
import fetch.schedule_runner as sr
from fetch.schedule_runner import daily_names, is_due


def _scheduled_set():
    """57 张＝BACKFILL_TARGETS − 日更组 − 周期组。"""
    daily = set(DAILY_UPDATE_EXTRA) | set(PERIODIC_UPDATE_TARGETS)
    for group in DAILY_UPDATE_GROUPS.values():
        daily |= set(group)
    return set(BACKFILL_TARGETS) - daily


def test_coverage_no_omission():
    """判据 1：57 张必须**全部**有归属（在册 36 ＋ 剔除 21）。"""
    cover = set(SCHEDULE) | set(EXCLUDED)
    assert cover == _scheduled_set()
    assert not (set(SCHEDULE) & set(EXCLUDED))
    assert len(SCHEDULE) == 36 and len(EXCLUDED) == 21


def test_freq_values_valid():
    assert all(v in FREQ_DAYS for v in SCHEDULE.values())
    assert set(SCHEDULE.values()) == {"D", "W", "M", "Q"}


def test_no_overlap_with_daily_groups():
    """互斥：在册表不得进入当日日更组。"""
    assert not (set(SCHEDULE) & daily_names())


def test_is_due_semantics():
    today = date(2026, 11, 10)
    assert is_due("D", None, today) is True
    assert is_due("M", (today - timedelta(days=29)).isoformat(), today) is False
    assert is_due("M", (today - timedelta(days=30)).isoformat(), today) is True
    assert is_due("Q", (today - timedelta(days=89)).isoformat(), today) is False
    assert is_due("Q", (today - timedelta(days=90)).isoformat(), today) is True
    assert is_due("W", "not-a-time", today) is True   # 不可解析按到期


class _FakeCp:
    """替身 Checkpoint：不碰真实断点文件（判据 7 零数据写入）。"""

    def __init__(self, *a, **kw):
        self.data = {}

    def mark_fetched(self, name):
        self.data.setdefault(name, {"completed": []})["last_fetch"] = "2026-11-10T12:00:00"

    def save(self):
        pass


def _run_one(monkeypatch, target, cp):
    monkeypatch.setattr(sr, "SCHEDULE", {"us_tradecal": "Q"})
    monkeypatch.setattr(sr, "Checkpoint", lambda *a, **kw: cp)
    monkeypatch.setattr(sr, "get_pro", lambda: object())
    monkeypatch.setattr(sr, "run_target", target)
    return sr.run(dry_run=False)


def test_breakpoint_success_writes_last_fetch(monkeypatch):
    cp = _FakeCp()
    assert _run_one(monkeypatch, lambda *a, **kw: {"done": 3}, cp) == 0
    rec = cp.data["us_tradecal"]
    assert rec.get("last_fetch") and "last_attempt" not in rec


def test_breakpoint_failure_writes_attempt_only(monkeypatch):
    cp = _FakeCp()

    def boom(*a, **kw):
        raise RuntimeError("源端 500")

    assert _run_one(monkeypatch, boom, cp) == 1
    rec = cp.data["us_tradecal"]
    assert rec.get("last_attempt") and "last_fetch" not in rec


def test_lock_rejects_concurrent_instance(tmp_path):
    lock = tmp_path / ".schedule.lock"
    with sr._InstanceLock(str(lock)):
        with pytest.raises(SystemExit):
            with sr._InstanceLock(str(lock)):
                pass
    assert not lock.exists()          # 正常退出后释放


def test_daily_group_tables_are_skipped(monkeypatch):
    """互斥日志证据：把一张日更表放进 SCHEDULE，select 必须判 skip_daily。"""
    name = sorted(daily_names())[0]
    monkeypatch.setitem(sr.SCHEDULE, name, "D")
    rows = {r[0]: r[4] for r in sr.select(cp=None)}
    assert rows[name] == "skip_daily"
