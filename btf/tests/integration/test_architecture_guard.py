# -*- coding: utf-8 -*-
"""架构守护验收（M1 任务 4.4）：故意违例三例全被拦截。

l2：写临时违例模块 → lint-imports / 白名单脚本子进程 → 断言失败且
点名违例 → finally 清理（违例文件绝不残留）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINT_IMPORTS = Path(sys.executable).parent / (
    "lint-imports.exe" if os.name == "nt" else "lint-imports")

#: 三例故意违例（4.4 验收口径：铁律 3 / 铁律 5 / 铁律 2 各一）
VIOLATIONS = [
    ("btf/domain/_guard_violation.py",
     "import pandas  # 违例1：第三方入 domain（铁律 3）\n",
     ["Domain zero-dependency", "pandas"]),
    ("btf/viz/_guard_violation.py",
     "from btf.engine.loop import Engine  # 违例2：viz → engine（铁律 5）\n",
     ["btf.viz", "btf.engine"]),
    ("btf/execution/_guard_violation.py",
     "from btf.risk.manager import RiskVerdict  # 违例3：引擎四模块互引（铁律 2）\n",
     ["Engine-layer four modules mutually independent"]),
]


def _run_lint() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(LINT_IMPORTS), "--config", "pyproject.toml"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")


@pytest.mark.l2
def test_three_deliberate_violations_intercepted():
    """三例违例同时写入 → 一次 lint-imports 全部拦截。"""
    files = [ROOT / rel for rel, _, _ in VIOLATIONS]
    try:
        for (_rel, src, _), f in zip(VIOLATIONS, files, strict=True):
            f.write_text(src, encoding="utf-8")
        r = _run_lint()
        assert r.returncode != 0, "违例未被拦截——架构守护失效"
        out = r.stdout + r.stderr
        for rel, _, needles in VIOLATIONS:
            for needle in needles:
                assert needle in out, f"{rel} 违例未点名（缺 {needle!r}）"
        assert "Contracts: 6 kept, 0 broken." not in out
    finally:
        for f in files:
            f.unlink(missing_ok=True)


@pytest.mark.l2
def test_guard_clean_after_cleanup():
    """清理后守护恢复全绿（违例文件零残留）。"""
    r = _run_lint()
    assert r.returncode == 0
    assert "0 broken" in (r.stdout + r.stderr)


@pytest.mark.l2
def test_domain_whitelist_intercepts_stdlib_violation():
    """白名单对偶面：domain import json（标准库非白名单）也被拦截。"""
    f = ROOT / "btf" / "domain" / "_wl_violation.py"
    try:
        f.write_text("import json  # 白名单外标准库\n", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "tools/check_domain_whitelist.py"],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        assert r.returncode == 1
        assert "_wl_violation.py" in r.stdout
        assert "json" in r.stdout
    finally:
        f.unlink(missing_ok=True)


@pytest.mark.l2
def test_plugin_boundary_intercepts_core_registration():
    """注册点唯一性：核心包内 register() 调用被插件边界检查拦截。

    基线纪律：main() 注册点检查先失败 → --update 不落盘违例态；
    本测试**不得**重刷基线（曾用 --update "恢复"——会静默吸收任何
    并行核心改动，使基线漂移检查失效）。保存/恢复原件兜底。
    """
    baseline = ROOT / "tools" / ".plugin_baseline.json"
    saved = baseline.read_bytes() if baseline.exists() else None
    f = ROOT / "btf" / "strategy" / "_boundary_violation.py"
    try:
        f.write_text(
            "from btf import registry\n"
            "registry.register('data_feed', 'rogue', object)\n",
            encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "tools/check_plugin_boundary.py", "--update"],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        # 注册点检查与基线独立：核心注册必须 FAIL（exit 1）
        assert r.returncode == 1
        assert "_boundary_violation.py" in r.stdout
        # 违例态不得写入基线（注册点先失败短路 --update）
        assert (not baseline.exists()
                or "_boundary_violation" not in baseline.read_text("utf-8"))
    finally:
        f.unlink(missing_ok=True)
        if saved is not None:
            baseline.write_bytes(saved)       # 恢复原基线（不重刷）
