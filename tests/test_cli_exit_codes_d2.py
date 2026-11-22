# -*- coding: utf-8 -*-
"""tests/test_cli_exit_codes_d2.py — D2 退出码与失败传播负向用例（R6-D2）。"""

import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli.app as cli_app
import cli.fetch_cmds as fetch_cmds

ROOT = Path(__file__).resolve().parents[1]


def _gap_args(**kw):
    base = dict(date=None, group="core", dry_run=True, lookback=1, only=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------- gp-update / intraday-update ----------------


def test_gap_update_fail_returns_1(monkeypatch):
    monkeypatch.setattr(
        "fetch.daily_update.run_daily_update", lambda **kw: {"ok": 1, "fail": 1, "rows": 0}
    )
    assert fetch_cmds.cmd_gap_update(_gap_args()) == 1


def test_gap_update_ok_returns_0(monkeypatch):
    monkeypatch.setattr(
        "fetch.daily_update.run_daily_update", lambda **kw: {"ok": 3, "fail": 0, "rows": 10}
    )
    assert fetch_cmds.cmd_gap_update(_gap_args()) == 0


def test_intraday_update_fail_returns_1(monkeypatch):
    monkeypatch.setattr(
        "fetch.daily_update.run_daily_update", lambda **kw: {"ok": 0, "fail": 2, "rows": 0}
    )
    assert fetch_cmds.cmd_intraday_update(_gap_args()) == 1


# ---------------- redtide-supply ----------------


def test_redtide_supply_step_exception_returns_1(monkeypatch):
    def boom(**kw):
        raise RuntimeError("source down")

    monkeypatch.setattr("fetch.redtide_supply.run_adj_factor", lambda **kw: None)
    monkeypatch.setattr("fetch.redtide_supply.run_stk_limit", boom)
    monkeypatch.setattr("fetch.redtide_supply.run_market_state", lambda **kw: None)
    assert fetch_cmds.cmd_redtide_supply(SimpleNamespace(dry_run=True)) == 1


def test_redtide_supply_all_ok_returns_0(monkeypatch):
    monkeypatch.setattr("fetch.redtide_supply.run_adj_factor", lambda **kw: None)
    monkeypatch.setattr("fetch.redtide_supply.run_stk_limit", lambda **kw: None)
    monkeypatch.setattr("fetch.redtide_supply.run_market_state", lambda **kw: None)
    assert fetch_cmds.cmd_redtide_supply(SimpleNamespace(dry_run=True)) == 0


# ---------------- fund-nav ----------------


def _fund_args(**kw):
    base = dict(repair=False, repair_threshold=5, dry_run=True, limit=None, date=None, days=5)
    base.update(kw)
    return SimpleNamespace(**base)


def test_fund_nav_failed_days_returns_1(monkeypatch):
    # D4/F1：真实契约（run_fund_nav_update 返回 dict，含 fail/failed_days）
    monkeypatch.setattr(
        "fetch.fund_nav_update.run_fund_nav_update",
        lambda **kw: {
            "fail": 2,
            "ok": 3,
            "failed_days": ["20261120"],
            "partial_days": [],
            "day_status": {},
            "rows": 0,
            "partitions": 0,
            "dry_run": True,
        },
    )
    assert fetch_cmds.cmd_fund_nav(_fund_args()) == 1


def test_fund_nav_repair_failed_returns_1(monkeypatch):
    monkeypatch.setattr(
        "fetch.fund_nav_update.repair_incomplete",
        lambda **kw: {"fail": 1, "fixed": 0, "rows": 0, "targets": 1, "dry_run": False},
    )
    assert fetch_cmds.cmd_fund_nav(_fund_args(repair=True)) == 1


def test_fund_nav_ok_returns_0(monkeypatch):
    monkeypatch.setattr(
        "fetch.fund_nav_update.run_fund_nav_update",
        lambda **kw: {
            "fail": 0,
            "ok": 5,
            "failed_days": [],
            "partial_days": [],
            "day_status": {},
            "rows": 100,
            "partitions": 3,
            "dry_run": False,
        },
    )
    assert fetch_cmds.cmd_fund_nav(_fund_args()) == 0


# ---------------- download-full / download-cb / backfill ----------------


def test_download_full_exception_returns_1(monkeypatch):
    def boom(**kw):
        raise RuntimeError("network")

    monkeypatch.setattr("fetch.full_download.run_download", boom)
    args = SimpleNamespace(phase="daily", reset=False, dry_run=True, workers=None)
    assert fetch_cmds.cmd_download_full(args) == 1


def test_download_cb_exception_returns_1(monkeypatch):
    def boom(**kw):
        raise RuntimeError("network")

    monkeypatch.setattr("fetch.cb_download.run_cb_download", boom)
    args = SimpleNamespace(reset=False, dry_run=True, workers=None)
    assert fetch_cmds.cmd_download_cb(args) == 1


def _backfill_args(**kw):
    base = dict(
        list_targets=False,
        tier="A",
        only=None,
        dry_run=True,
        workers=None,
        parallel_targets=None,
        limit=None,
        force=False,
        codes_file=None,
        force_listed=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_backfill_exception_returns_1(monkeypatch):
    def boom(**kw):
        raise RuntimeError("network")

    monkeypatch.setattr("fetch.backfill.run_backfill", boom)
    assert fetch_cmds.cmd_backfill(_backfill_args()) == 1


def test_backfill_ok_returns_0(monkeypatch):
    monkeypatch.setattr("fetch.backfill.run_backfill", lambda **kw: None)
    assert fetch_cmds.cmd_backfill(_backfill_args()) == 0


# ---------------- alt.update ----------------


def test_alt_update_error_table_returns_1(monkeypatch):
    import alt.update as alt_update

    monkeypatch.setattr(
        alt_update,
        "run",
        lambda **kw: [
            {"table": "us_daily", "action": "by_year", "ok": False, "errors": ["boom"]},
            {"table": "hk_daily", "action": "skip", "ok": True, "errors": []},
        ],
    )
    assert alt_update.main(["--dry-run"]) == 1


def test_alt_update_all_ok_or_skip_returns_0(monkeypatch):
    import alt.update as alt_update

    monkeypatch.setattr(
        alt_update,
        "run",
        lambda **kw: [
            {"table": "us_daily", "action": "by_year", "ok": True, "errors": []},
            {"table": "hk_daily", "action": "skip", "ok": True, "errors": []},
        ],
    )
    assert alt_update.main(["--dry-run"]) == 0


# ---------------- cli.app 透传 ----------------


def _fake_parser(func):
    class _Parser:
        def parse_args(self, argv=None):
            return SimpleNamespace(command="fake", func=func, json=True)

        def print_help(self):
            pass

    return _Parser()


def test_app_passes_nonzero_rc(monkeypatch):
    monkeypatch.setattr(cli_app, "build_parser", lambda: _fake_parser(lambda args: 1))
    monkeypatch.setattr(cli_app, "setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit) as excinfo:
        cli_app.main([])
    assert excinfo.value.code == 1


def test_app_none_rc_is_zero(monkeypatch):
    monkeypatch.setattr(cli_app, "build_parser", lambda: _fake_parser(lambda args: None))
    monkeypatch.setattr(cli_app, "setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit) as excinfo:
        cli_app.main([])
    assert excinfo.value.code == 0


# ---------------- 子进程：业务失败必须 rc≠0 ----------------


def test_subprocess_business_failure_nonzero():
    script = textwrap.dedent("""
        import sys
        from types import SimpleNamespace

        import fetch.daily_update as du
        du.run_daily_update = lambda **kw: {"ok": 0, "fail": 1, "rows": 0}

        from cli.fetch_cmds import cmd_gap_update
        args = SimpleNamespace(date=None, group="core", dry_run=True,
                               lookback=1, only=None)
        sys.exit(cmd_gap_update(args))
    """)
    r = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert r.returncode == 1, f"rc={r.returncode} stderr={r.stderr[-800:]}"
