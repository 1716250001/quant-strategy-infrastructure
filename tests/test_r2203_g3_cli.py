# -*- coding: utf-8 -*-
"""R2203 G3：引擎可用性（db health / --apply--execute / --table / help / report --quiet）。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from cli.parser import build_parser


def _parse(argv):
    return build_parser().parse_args(argv)


class TestApplyExecuteSynonyms:
    @pytest.mark.parametrize(
        ("argv", "attr"),
        [
            (["db", "backup", "--apply"], "execute"),
            (["db", "backup", "--execute"], "execute"),
            (["db", "ckpt", "--apply"], "execute"),
            (["db", "ckpt", "--execute"], "execute"),
            (["db", "stale-fix", "--apply"], "apply"),
            (["db", "stale-fix", "--execute"], "apply"),
            (["db", "meta-close", "--apply"], "apply"),
            (["db", "meta-close", "--execute"], "apply"),
            (["schedule", "--apply"], "apply"),
            (["schedule", "--execute"], "apply"),
        ],
    )
    def test_synonyms_set_same_dest(self, argv, attr):
        assert getattr(_parse(argv), attr) is True

    @pytest.mark.parametrize(
        "argv",
        [
            ["db", "backup", "--apply", "--dry-run"],
            ["db", "ckpt", "--apply", "--dry-run"],
            ["db", "stale-fix", "--execute", "--dry-run"],
            ["db", "meta-close", "--execute", "--dry-run"],
            ["schedule", "--execute", "--dry-run"],
        ],
    )
    def test_dry_run_flag_still_available(self, argv):
        assert _parse(argv).dry_run is True


def test_stale_fix_execute_with_dry_run_does_not_apply(monkeypatch):
    from cli.db_cmds import cmd_db_stale_fix

    called = []
    monkeypatch.setattr("tools.research.fund_stale.detect", lambda: [])
    monkeypatch.setattr(
        "tools.research.fund_stale.apply_fix", lambda *a, **k: called.append(1) or {}
    )
    argv = ["db", "stale-fix", "--execute", "--dry-run"]
    assert cmd_db_stale_fix(_parse(argv)) == 0
    assert called == []


def test_meta_close_execute_with_dry_run_not_applied(monkeypatch):
    from cli.db_cmds import cmd_db_meta_close

    seen = {}
    monkeypatch.setattr("tools.db.meta_close.run", lambda **kw: seen.update(kw) or 0)
    with pytest.raises(SystemExit):
        cmd_db_meta_close(_parse(["db", "meta-close", "--execute", "--dry-run"]))
    assert seen["apply"] is False


class TestGapUpdateTableAlias:
    def test_table_maps_to_only(self, monkeypatch, capsys):
        from cli.fetch_cmds import cmd_gap_update

        seen = {}
        monkeypatch.setattr(
            "fetch.daily_update.run_daily_update",
            lambda **kw: seen.update(kw) or {"fail": 0},
        )
        assert cmd_gap_update(_parse(["gap-update", "--table", "daily", "--dry-run"])) == 0
        assert seen["only"] == "daily" and seen["dry_run"] is True

    def test_only_wins_when_same(self, monkeypatch):
        from cli.fetch_cmds import cmd_gap_update

        seen = {}
        monkeypatch.setattr(
            "fetch.daily_update.run_daily_update",
            lambda **kw: seen.update(kw) or {"fail": 0},
        )
        argv = ["gap-update", "--only", "daily", "--table", "daily", "--dry-run"]
        assert cmd_gap_update(_parse(argv)) == 0
        assert seen["only"] == "daily"

    def test_conflict_exits_two(self, capsys):
        from cli.fetch_cmds import cmd_gap_update

        argv = ["gap-update", "--only", "daily", "--table", "index_daily"]
        assert cmd_gap_update(_parse(argv)) == 2
        assert "--only 与 --table" in capsys.readouterr().err


def test_db_report_quiet_passes_verbose_false(monkeypatch, capsys):
    from cli.db_cmds import cmd_db_report

    seen = {}
    monkeypatch.setattr("tools.db.gen_report.run", lambda **kw: seen.update(kw) or None)
    cmd_db_report(_parse(["db", "report", "--quiet"]))
    out = capsys.readouterr().out
    assert seen["verbose"] is False and seen["as_json"] is False
    assert "db-report 数据库结构报告" not in out


class TestDbHealth:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cli.db_cmds._health_cache_path", lambda: str(tmp_path / "latest.json"))
        monkeypatch.setattr(
            "tools.doctor.run_doctor", lambda **kw: SimpleNamespace(n_fail=0, n_warn=0)
        )
        monkeypatch.setattr("tools.quicklook.run_freshness", lambda **kw: [])
        self.cache = tmp_path / "latest.json"

    def _args(self, *extra):
        return _parse(["db", "health", "--json", *extra])

    def test_default_missing_cache_strict_exits_one(self, capsys):
        from cli.db_cmds import cmd_db_health

        assert cmd_db_health(self._args("--strict")) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["cache"]["status"] == "未运行"
        assert payload["full"] is False and payload["ok"] is False

    def test_fresh_cache_non_strict_ok(self, capsys):
        from cli.db_cmds import cmd_db_health

        self.cache.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "audit": {"ok": True, "n_issue": 0},
                    "fingerprint": {"ok": True, "rc": 0},
                }
            ),
            encoding="utf-8",
        )
        assert cmd_db_health(self._args("--strict")) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["cache"]["status"] == "ok" and payload["ok"] is True

    def test_expired_cache_strict_exits_one(self, capsys):
        from cli.db_cmds import cmd_db_health

        old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        self.cache.write_text(
            json.dumps(
                {
                    "generated_at": old,
                    "audit": {"ok": True, "n_issue": 0},
                    "fingerprint": {"ok": True, "rc": 0},
                }
            ),
            encoding="utf-8",
        )
        assert cmd_db_health(self._args("--strict")) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["cache"]["status"] == "过期" and payload["ok"] is False

    def test_full_runs_and_writes_cache(self, monkeypatch, capsys):
        from cli.db_cmds import cmd_db_health

        monkeypatch.setattr("cli.db_cmds._run_audit_quiet", lambda: {"ok": True, "n_issue": 0})
        monkeypatch.setattr("cli.db_cmds._run_fingerprint_quiet", lambda: {"ok": True, "rc": 0})
        assert cmd_db_health(self._args("--full", "--strict")) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["full"] is True and payload["cache"]["status"] == "ok"
        saved = json.loads(self.cache.read_text(encoding="utf-8"))
        assert saved["audit"]["ok"] is True and saved["fingerprint"]["ok"] is True

    def test_red_freshness_strict_exits_one(self, monkeypatch, capsys):
        from cli.db_cmds import cmd_db_health

        monkeypatch.setattr(
            "tools.quicklook.run_freshness", lambda **kw: [{"verdict": "红"}, {"verdict": "绿"}]
        )
        self.cache.write_text(
            json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds")}),
            encoding="utf-8",
        )
        # 有缓存但缺 audit/fingerprint 块 ⇒ 仅看 freshness
        assert cmd_db_health(self._args("--strict")) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["freshness"]["n_red"] == 1 and payload["ok"] is False

    def test_audit_or_fingerprint_failure_unhealthy(self, monkeypatch, capsys):
        from cli.db_cmds import cmd_db_health

        monkeypatch.setattr("cli.db_cmds._run_audit_quiet", lambda: {"ok": False, "n_issue": 2})
        monkeypatch.setattr("cli.db_cmds._run_fingerprint_quiet", lambda: {"ok": True, "rc": 0})
        assert cmd_db_health(self._args("--full", "--strict")) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["audit"]["n_issue"] == 2 and payload["ok"] is False


def test_db_group_lists_all_14(capsys):
    from cli.db_cmds import cmd_db_group

    cmd_db_group(SimpleNamespace(db_command=None))
    out = capsys.readouterr().out
    expected = {
        "clean",
        "audit",
        "schema",
        "report",
        "migrate",
        "freshness",
        "scale-check",
        "schema-align",
        "fingerprint",
        "ckpt",
        "meta-close",
        "backup",
        "stale-fix",
        "health",
    }
    for name in expected:
        assert f"  {name}" in out
    assert "14 个子命令" in out


def test_help_grouping_and_command_counts():
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    visible = [a.dest for a in sub._choices_actions if a.help is not argparse.SUPPRESS]
    hidden = [a.dest for a in sub._choices_actions if a.help is argparse.SUPPRESS]
    assert len(visible) == 30, sorted(visible)
    assert len(hidden) == 10, sorted(hidden)
    for title in ("【数据维护】", "【研究分析】", "【推送导出】", "【诊断】"):
        assert title in parser.epilog
    assert "兼容别名（10" in parser.epilog
    for alias in hidden:
        assert alias in parser.epilog
