# -*- coding: utf-8 -*-
"""tests/test_structural_empty.py — F-1「结构性空」守卫（§418 F-1c）

钉死 `fetch/backfill.py::_resolve_empty_date` 的三条语义：
  ① 已实证 T+1 发布表（白名单）当日空 ⇒ 记 done、**不入** `source_empty_dates`、计数器 +1；
  ② 历史日空 ⇒ **不**记 done（fail-closed 仍生效，留待续跑）；
  ③ 非白名单表当日空 ⇒ **不**记 done（§418 F-1b：不得无差别豁免当日锚点）。

证明力：把改动回退到 `3fbc034`（`>= today` 无差别豁免 + 周月星期判据），
① 仍绿但 ③ 必红；再回退到 `b99c26a`（无结构性空分支）本文件全部 ImportError 红。
"""
from datetime import datetime, timedelta

import fetch.backfill as bf


class _FakeCP:
    """只记录断点写入，不落盘。"""

    def __init__(self):
        self.done = []

    def mark_done(self, name, key):
        self.done.append((name, key))


def _today():
    return datetime.now().strftime("%Y%m%d")


def test_t1_whitelist_today_empty_marked_done(monkeypatch):
    """① 白名单表当日空 ⇒ done + 结构性空计数 +1，且不写 source_empty_dates。"""
    monkeypatch.setattr(bf, "_is_registered_source_empty_date", lambda n, d: False)
    monkeypatch.setattr(bf, "_empty_as_done", lambda n: False)
    cp, ct = _FakeCP(), {"n": 0}
    dt = _today()

    tag = bf._resolve_empty_date("margin", dt, cp, ct)

    assert tag == "structural"
    assert cp.done == [("margin", dt)], "白名单表当日空必须记 done（次日由日更补回）"
    assert ct["n"] == 1, "结构性空必须计入计数器，否则该分支无观测手段"
    assert bf._table_source_empty_dates_registry("margin") is None, \
        "结构性空不得写入 source_empty_dates（设计如此，只有核验过的空日才登记）"


def test_non_whitelist_today_empty_stays_pending(monkeypatch):
    """③ 非白名单表当日空 ⇒ **不**记 done（F-1b：不得无差别豁免当日锚点）。"""
    monkeypatch.setattr(bf, "_is_registered_source_empty_date", lambda n, d: False)
    monkeypatch.setattr(bf, "_empty_as_done", lambda n: False)
    cp, ct = _FakeCP(), {"n": 0}

    tag = bf._resolve_empty_date("weekly", _today(), cp, ct)

    assert tag == "pending"
    assert cp.done == [], "非白名单表当日空不得记 done（会掩盖真实发布延迟）"
    assert ct["n"] == 0


def test_historical_empty_stays_pending(monkeypatch):
    """② 历史日空 ⇒ **不**记 done：fail-closed 仍生效，留待续跑。"""
    monkeypatch.setattr(bf, "_is_registered_source_empty_date", lambda n, d: False)
    monkeypatch.setattr(bf, "_empty_as_done", lambda n: False)
    cp, ct = _FakeCP(), {"n": 0}
    dt = (datetime.now() - timedelta(days=300)).strftime("%Y%m%d")

    tag = bf._resolve_empty_date("margin", dt, cp, ct)

    assert tag == "pending"
    assert cp.done == [], "历史日返空必须留待续跑（限频/重试耗尽正是要暴露的问题）"
    assert ct["n"] == 0


def test_future_date_is_structural(monkeypatch):
    """未来日 ⇒ done + 计数（源端不可能发布未来数据）。"""
    monkeypatch.setattr(bf, "_is_registered_source_empty_date", lambda n, d: False)
    monkeypatch.setattr(bf, "_empty_as_done", lambda n: False)
    cp, ct = _FakeCP(), {"n": 0}
    dt = (datetime.now() + timedelta(days=3)).strftime("%Y%m%d")

    assert bf._resolve_empty_date("weekly", dt, cp, ct) == "structural"
    assert cp.done == [("weekly", dt)]
    assert ct["n"] == 1


def test_registered_date_done_but_not_structural(monkeypatch):
    """已登记空日 ⇒ done，但**不**计入结构性空（两者口径不同，勿混算）。"""
    monkeypatch.setattr(bf, "_is_registered_source_empty_date", lambda n, d: True)
    monkeypatch.setattr(bf, "_empty_as_done", lambda n: False)
    cp, ct = _FakeCP(), {"n": 0}

    assert bf._resolve_empty_date("block_trade", "20050111", cp, ct) == "registered"
    assert cp.done == [("block_trade", "20050111")]
    assert ct["n"] == 0
