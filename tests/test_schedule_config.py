# -*- coding: utf-8 -*-
"""CC-FIX-13（R3'-b）：调度频率表覆盖率与到期判定。"""

from datetime import date, timedelta

import pytest

import fetch.schedule_runner as sr
from config.backfill_targets import BACKFILL_TARGETS
from config.daily import DAILY_UPDATE_EXTRA, DAILY_UPDATE_GROUPS, PERIODIC_UPDATE_TARGETS
from config.schedule import EXCLUDED, FREQ_DAYS, SCHEDULE
from fetch.schedule_runner import daily_names, is_due


def _scheduled_set():
    """56 张＝BACKFILL_TARGETS − 日更组 − 周期组（fund_share 已转入日更组）。"""
    daily = set(DAILY_UPDATE_EXTRA) | set(PERIODIC_UPDATE_TARGETS)
    for group in DAILY_UPDATE_GROUPS.values():
        daily |= set(group)
    return set(BACKFILL_TARGETS) - daily


def test_coverage_no_omission():
    """判据 1：56 张必须**全部**有归属（在册 33 ＋ 剔除 23，CC-FIX-15/F2-A）。"""
    cover = set(SCHEDULE) | set(EXCLUDED)
    assert cover == _scheduled_set()
    assert not (set(SCHEDULE) & set(EXCLUDED))
    assert len(SCHEDULE) == 33 and len(EXCLUDED) == 23


def test_freq_values_valid():
    assert all(v in FREQ_DAYS for v in SCHEDULE.values())
    assert set(SCHEDULE.values()) == {"D", "W", "M", "Q"}


def test_cn_pmi_blocked_and_dual_channel_excluded():
    """CC-FIX-15 A1/A2/A5：cn_pmi 退出排班但保留挂账；双通道按实证归属。

    ⚠ REQ-20261126-01（2026-11-28）更正：`fund_share` 的 EXCLUDED 理由
    “由 fund-nav 独立通道承载”是错误前提（fund_nav_update.py 从不写 fund_share）。
    该表已改 by_date 并纳入 extend 日更组，**只保留 fund_nav 在 EXCLUDED**。
    """
    from config.tables import PENDING_REFRESH

    assert "cn_pmi" not in SCHEDULE and "cn_pmi" in EXCLUDED
    assert "源端列集变更" in EXCLUDED["cn_pmi"]
    assert "挂账不动" in PENDING_REFRESH["cn_pmi"]["plan"]
    assert "fund_nav" not in SCHEDULE and "fund_nav" in EXCLUDED
    assert "fund_share" not in EXCLUDED
    assert "fund_share" in DAILY_UPDATE_GROUPS["extend"]


def test_fund_share_must_not_return_to_excluded():
    """负例（REQ-20261126-01 T5）：禁止把 fund_share 重新写回 EXCLUDED。

    该表曾因“由 fund-nav 承载”的**未经实证**理由被移出排班，实际主通道
    fund_nav_update.py 从不写 fund_share ⇒ 77.5% 标的自 20260918 起停更。
    若有人再次把 fund_share 加进 EXCLUDED（无论理由措辞），本用例必须报红。
    """
    assert "fund_share" in DAILY_UPDATE_GROUPS["extend"], (
        "fund_share 必须由 extend 日更组承载（by_date，单日约 1,744 只）"
    )
    assert "fund_share" not in EXCLUDED, (
        "fund_share 不得重新进入 EXCLUDED：所谓“由 fund-nav 承载”是错误前提，"
        "fetch/fund_nav_update.py 全文不写 fund_share（REQ-20261126-01 已实证）"
    )
    from config.backfill_targets import BACKFILL_TARGETS as _BT

    assert _BT["fund_share"]["mode"] == "by_date", (
        "fund_share 的日更形态必须是 by_date；by_code 会被断点/库内已有双闸门锁死"
    )


def test_grayscale_candidates_are_safe():
    """CC-FIX-15 A4：灰度候选恰 5 张，均非 once/静态/停更/待补拉/剔除，且当前到期。"""
    from config.schedule import GRAYSCALE
    from config.tables import PENDING_REFRESH, STALE_BY_DESIGN, STATIC_TABLES

    assert len(GRAYSCALE) == 5 and len(set(GRAYSCALE)) == 5
    for name in GRAYSCALE:
        conf = BACKFILL_TARGETS[name]
        assert conf["mode"] != "once", name
        assert name not in STATIC_TABLES and name not in STALE_BY_DESIGN
        assert name not in PENDING_REFRESH and name not in EXCLUDED
        assert name in SCHEDULE
        assert is_due(SCHEDULE[name], None) is True, name


def test_a9_cn_pmi_visible_when_forced_back(monkeypatch):
    """A9 负例：强行塞回 SCHEDULE 后 select 必须报出它（证明无别处静默过滤）。"""
    monkeypatch.setitem(sr.SCHEDULE, "cn_pmi", "M")
    rows = {r[0]: r[4] for r in sr.select(cp=None)}
    assert rows["cn_pmi"] == "ok"


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
    assert is_due("W", "not-a-time", today) is True  # 不可解析按到期


class _FakeCp:
    """替身 Checkpoint：不碰真实断点文件（判据 7 零数据写入）。"""

    def __init__(self, *a, **kw):
        self.data = {}

    def is_done(self, name, key, library=None, key_space=None):
        # P3-4（§455）：签名与 Checkpoint 同步；断言不变。
        return False  # CC-FIX-16：估算器不再吞异常，替身须补此接口

    def mark_done(self, name, key, library=None, key_space=None):
        pass

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
    assert not lock.exists()  # 正常退出后释放


def test_daily_group_tables_are_skipped(monkeypatch):
    """互斥日志证据：把一张日更表放进 SCHEDULE，select 必须判 skip_daily。"""
    name = sorted(daily_names())[0]
    monkeypatch.setitem(sr.SCHEDULE, name, "D")
    rows = {r[0]: r[4] for r in sr.select(cp=None)}
    assert rows[name] == "skip_daily"
