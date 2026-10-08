# -*- coding: utf-8 -*-
"""**R42′（N45）**：`tools/check_v77_digest.py` **默认入口** smoke（进 CI）。

背景（N45，P1）：工具默认路径（不落盘）曾 100% 崩溃——`rt.run(persist=False)`
返回的 `RunResult` 无 `.metrics`；且退出码被 `tail` 管道吞掉 ⇒ 崩溃被判「通过」。
本用例：
  ① 走**默认入口**（`--smoke`：不落盘＋短窗单档）——专测默认分支不崩；
  ② 取**独立退出码**（subprocess returncode，不经管道）——判成败。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]      # btf/
TOOL = ROOT / "tools" / "check_v77_digest.py"


@pytest.mark.smoke
def test_default_entry_smoke_independent_exit_code():
    p = subprocess.run(
        [sys.executable, str(TOOL), "--smoke"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900)
    assert p.returncode == 0, (
        f"默认入口 smoke 失败（独立退出码={p.returncode}）：\n"
        f"stdout 尾部：\n{p.stdout[-1500:]}\nstderr 尾部：\n{p.stderr[-800:]}")
    assert "smoke OK" in p.stdout
    assert "digest = sha256:" in p.stdout
