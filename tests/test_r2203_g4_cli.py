# -*- coding: utf-8 -*-
"""R2203 G4：引擎侧收口（05 §8.3 U4/U6；--dry-run 行为级）。"""

from __future__ import annotations

import json

from cli.parser import build_parser


def _parse(argv):
    return build_parser().parse_args(argv)


def test_db_backup_apply_with_dry_run_does_not_mirror(monkeypatch):
    from cli.db_cmds import cmd_db_backup

    called = []
    monkeypatch.setattr(
        "tools.doctor.backup_status",
        lambda: {"exists": False, "root": "X:/no-such", "latest": None},
    )
    monkeypatch.setattr("tools.data_backup.mirror", lambda: called.append(1) or 0)
    assert cmd_db_backup(_parse(["db", "backup", "--mirror", "--apply", "--dry-run"])) == 0
    assert called == []


def test_db_ckpt_apply_with_dry_run_does_not_write(monkeypatch):
    import tools.ckpt_tool as ckpt_mod
    from cli.db_cmds import cmd_ckpt

    seen = {}
    monkeypatch.setattr(ckpt_mod, "_do_show", lambda **kw: [])
    monkeypatch.setattr(ckpt_mod, "_do_fix", lambda **kw: seen.update(kw) or 0)
    cmd_ckpt(_parse(["db", "ckpt", "--fix", "--apply", "--dry-run"]))
    assert seen["execute"] is False


def test_schedule_execute_with_dry_run_passes_dry_run(monkeypatch):
    import fetch.schedule_runner as runner
    from cli.fetch_cmds import cmd_schedule

    seen = {}

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(runner, "_InstanceLock", _Lock)
    monkeypatch.setattr(runner, "run", lambda *a, **kw: seen.update(kw) or 0)
    assert cmd_schedule(_parse(["schedule", "--execute", "--dry-run"])) == 0
    assert seen["dry_run"] is True


def test_health_component_exception_marks_unhealthy(monkeypatch, tmp_path, capsys):
    from cli.db_cmds import cmd_db_health

    monkeypatch.setattr("cli.db_cmds._health_cache_path", lambda: str(tmp_path / "health.json"))

    def _boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("tools.doctor.run_doctor", _boom)
    monkeypatch.setattr("tools.quicklook.run_freshness", lambda **kw: [])
    assert cmd_db_health(_parse(["db", "health", "--json", "--strict"])) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["errors"] and "doctor" in payload["errors"][0]
