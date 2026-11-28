# -*- coding: utf-8 -*-
"""REQ-20261126-01 T10：日更汇总不得吞掉表内部分失败。

规则（00 §10.2 T10）：
  - core 组 fail>0 → 计失败（gap-update rc=1）；
  - extend 组 fail>0 → 不计失败（非阻断），但必须打印 `[!] 部分失败` 且进日志清单。
"""

import fetch.daily_update as du


def test_summarize_core_vs_extend():
    plan = [("daily", {}, ["20261127"], {}), ("fund_share", {}, ["20261127"], {})]
    results = [
        ("daily", {"done": 1, "fail": 0}),
        ("fund_share", {"done": 5, "fail": 1}),
    ]
    ok, fail, partial_core, partial_extend, missing = du._summarize_results(
        plan, results, {"daily"}
    )
    assert (ok, fail, missing) == (2, 0, [])
    assert partial_core == []
    assert partial_extend == [("fund_share", 1)]


def test_summarize_core_partial_counts_as_fail():
    plan = [("daily", {}, ["20261127"], {})]
    results = [("daily", {"done": 3, "fail": 2})]
    ok, fail, partial_core, partial_extend, _missing = du._summarize_results(
        plan, results, {"daily"}
    )
    assert (ok, fail) == (0, 1)
    assert partial_core == [("daily", 2)]
    assert partial_extend == []


def test_summarize_missing_result_counts_as_fail():
    plan = [("daily", {}, ["20261127"], {}), ("margin", {}, ["20261127"], {})]
    results = [("daily", {"done": 1, "fail": 0})]
    ok, fail, _pc, _pe, missing = du._summarize_results(plan, results, {"daily", "margin"})
    assert (ok, fail) == (1, 1)
    assert missing == ["margin"]


def _patch_run(monkeypatch, run_result):
    monkeypatch.setattr(du, "get_latest_trade_date", lambda x=None: "20261127")
    monkeypatch.setattr(du, "check_coverage", lambda *a, **k: None)
    monkeypatch.setattr(
        du,
        "resolve_gaps",
        lambda name, conf, td, lb: (
            ["20261127"],
            {"latest": "20261126", "gap": ["20261127"], "refresh": []},
        ),
    )
    monkeypatch.setattr(du, "get_pro", lambda: object())

    class _CP:
        path = "fake-checkpoint.json"

        def save(self):
            pass

    monkeypatch.setattr(du, "Checkpoint", lambda *a, **k: _CP())
    monkeypatch.setattr(du, "run_target", lambda *a, **k: dict(run_result))
    monkeypatch.setattr(du, "_report_permission_denied", lambda **k: None)

    events = []

    def _log(msg):
        events.append(msg)

    import common.db_version as dbv
    import common.logging_setup as ls

    monkeypatch.setattr(ls, "log_event", _log)
    monkeypatch.setattr(dbv, "record", lambda *a, **k: None)
    return events


def test_core_partial_fail_makes_gap_update_fail(monkeypatch, capsys):
    events = _patch_run(monkeypatch, {"done": 3, "fail": 2})
    res = du.run_daily_update(
        groups=("core",), only="daily", with_market_state=False, with_periodic=False
    )
    assert res["fail"] == 1, "core 组部分失败必须计失败（rc=1）"
    out = capsys.readouterr().out
    assert "[!]" in out and "部分失败" in out and "core 阻断" in out
    assert events and "partial_core" in events[0] and "daily" in events[0]


def test_extend_partial_fail_visible_but_not_blocking(monkeypatch, capsys):
    events = _patch_run(monkeypatch, {"done": 5, "fail": 1})
    res = du.run_daily_update(
        groups=("all",), only="fund_share", with_market_state=False, with_periodic=False
    )
    assert res["fail"] == 0, "extend 组部分失败不得阻断主流程"
    out = capsys.readouterr().out
    assert "[!]" in out and "部分失败" in out and "extend 非阻断" in out
    assert events and "fund_share" in events[0], "extend 部分失败也必须进日志失败清单"
