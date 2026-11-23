# -*- coding: utf-8 -*-
"""R2203 G4（05 §7.4 N2）：`bt check` 默认 10 项、`--full` 追加 L5 的命令序列。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.l1]


def _load_check():
    """`btf/tools/check.py` 是脚本（非包），按文件路径加载。"""
    path = Path(__file__).resolve().parents[2] / "tools" / "check.py"
    spec = importlib.util.spec_from_file_location("btf_check_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch(monkeypatch, calls):
    check_mod = _load_check()
    monkeypatch.setattr(check_mod, "_ruff_all", lambda: True)
    monkeypatch.setattr(
        check_mod, "run", lambda name, cmd, *a, **kw: calls.append((name, cmd)) or True
    )
    return check_mod


def test_check_default_has_no_l5(monkeypatch):
    calls: list = []
    mod = _patch(monkeypatch, calls)
    assert mod.main([]) == 0
    assert len(calls) == 9  # 第 1 项 ruff 合并计 1，其余 9 项
    assert not any("-m" in cmd and "l5" in cmd for _name, cmd in calls)


def test_check_full_appends_l5(monkeypatch):
    calls: list = []
    mod = _patch(monkeypatch, calls)
    assert mod.main(["--full"]) == 0
    assert len(calls) == 10
    name, cmd = calls[-1]
    assert "L5" in name
    assert cmd[1:4] == ["-m", "pytest", "tests"] and "l5" in cmd
