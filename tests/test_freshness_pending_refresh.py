# -*- coding: utf-8 -*-
"""CC-FIX-14（§380）：「待补拉」判定档 —— 只改写「黄 ∧ 命中登记」，strict 不受染。

T1 命中      ：PENDING_REFRESH 命中 + 黄 → 待补拉
T2 未命中    ：黄但不在登记 → 仍「黄」（防吞警负例）
T3 正常不受染：命中登记但判定正常 → 仍「正常」（补拉后自动回正常）
T4 strict    ：全部为「待补拉」时 run(strict=True) 返回 0
T5 strict 未改：真黄表仍使 strict 返回 1（strict 逻辑本身未被改写）
"""
import time
from datetime import date, timedelta

import tools.db.freshness as fr
from config.tables import PENDING_REFRESH


def _row(name, ds_days=71, dw_days=54, lib="market", tier="backfill"):
    """构造一条 market 行：内容日期 ds_days 天前、文件 mtime dw_days 天前。"""
    as_of = (date.today() - timedelta(days=ds_days)).strftime("%Y%m%d")
    return {"lib": lib, "table": name, "as_of": as_of, "last_pull": None,
            "last_pull_rows": None, "tier": tier,
            "newest_mtime": time.time() - dw_days * 86400,
            "source": "single", "stale_days": None}


def _build(monkeypatch, rows):
    monkeypatch.setattr(fr, "_market_rows", lambda only=None: list(rows))
    monkeypatch.setattr(fr, "_alt_rows", lambda only=None: [])
    return {r["table"]: r for r in fr.build_rows()}


def test_t1_hit_yellow_becomes_pending(monkeypatch):
    assert "cn_ppi" in PENDING_REFRESH
    got = _build(monkeypatch, [_row("cn_ppi")])
    assert got["cn_ppi"]["verdict"] == "待补拉"
    assert PENDING_REFRESH["cn_ppi"]["plan"] in got["cn_ppi"]["note"]


def test_t2_unregistered_yellow_stays_yellow(monkeypatch):
    """防吞警：未登记的黄表必须照常显示「黄」。"""
    got = _build(monkeypatch, [_row("not_in_pending_refresh")])
    assert got["not_in_pending_refresh"]["verdict"] == "黄"
    assert got["not_in_pending_refresh"]["note"] == ""


def test_t3_registered_but_normal_untouched(monkeypatch):
    """命中登记但刚写过（dw≤7）→ 仍「正常」，登记不产生任何注记。"""
    got = _build(monkeypatch, [_row("cn_ppi", dw_days=0)])
    assert got["cn_ppi"]["verdict"] == "正常"
    assert got["cn_ppi"]["note"] == ""


def test_t4_strict_ignores_pending(monkeypatch):
    monkeypatch.setattr(fr, "build_rows", lambda only=None: [
        {"table": "cn_ppi", "verdict": "待补拉"},
        {"table": "cn_m", "verdict": "待补拉"},
    ])
    monkeypatch.setattr(fr, "render", lambda rows: "")
    assert fr.run(strict=True) == 0


def test_t5_strict_still_catches_yellow(monkeypatch):
    """strict 逻辑本身未改：真黄仍 RC=1。"""
    monkeypatch.setattr(fr, "build_rows", lambda only=None: [
        {"table": "cn_ppi", "verdict": "待补拉"},
        {"table": "other", "verdict": "黄"},
    ])
    monkeypatch.setattr(fr, "render", lambda rows: "")
    assert fr.run(strict=True) == 1


def test_diag_dir_missing_state():
    assert fr._diag({"lib": "market", "source": "目录不存在", "dw": None}) == "目录缺失"
