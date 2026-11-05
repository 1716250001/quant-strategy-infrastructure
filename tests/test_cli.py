# -*- coding: utf-8 -*-
"""tests/test_cli.py — CLI 入口 smoke（W6-DEP-02）。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_help_exit_zero():
    r = subprocess.run([sys.executable, str(ROOT / "main.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-800:]


def test_cli_has_expected_command_count():
    r = subprocess.run([sys.executable, str(ROOT / "main.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    cmds = [ln.strip() for ln in r.stdout.splitlines() if ln.startswith("    ") and ln.strip()[:1].islower()]
    assert len(cmds) >= 25, f"命令数异常: {len(cmds)}"
