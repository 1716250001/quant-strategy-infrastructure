# -*- coding: utf-8 -*-
"""引擎文档计数守卫（D9）：`doctor` 13 组 / 32 项、`db audit` 5 段。

只读文档，不触真实数据/不运行引擎命令。权威口径：
- `tools/doctor.py::run_doctor` 顺序调用 **13** 个 `check_*` 分组（每项 1 组），
  `main.py doctor --json` 当前输出 **32** 个 item（2026-11-22 实测）；
- `tools/db/audit.py` / `main.py db audit --help` 的 `--section` 合法值：
  `checkpoint` / `dup` / `truncation` / `gap` / `scale` = **5 段**。

失败信息统一含「文档路径＋声明值＋实测值」。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_DOCS = ROOT / "docs" / "代码架构" / "全量数据库架构"
OPS_README = ROOT / "docs" / "运维手册" / "README.md"

DOCTOR_GROUPS = 13
DOCTOR_ITEMS = 32
AUDIT_SECTIONS = 5

_DOCTOR_PAT = re.compile(r"doctor[^\n]{0,16}?(\d{1,2})\s*组\s*[/／]\s*(\d{1,3})\s*项")
_AUDIT_PAT = re.compile(r"db audit[^\n]{0,24}?(\d)\s*段")


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_doctor_group_and_item_counts_in_docs():
    """文档声明的 `doctor` 组数/项数 == 13/32。"""
    paths = [
        DB_DOCS / "01-总览与定位.md",
        DB_DOCS / "04-领域模型与核心接口.md",
        DB_DOCS / "06-核心流程与关键时序.md",
        OPS_README,
    ]
    problems: list[str] = []
    for path in paths:
        hits = 0
        for i, ln in enumerate(_lines(path), 1):
            for m in _DOCTOR_PAT.finditer(ln):
                hits += 1
                groups, items = int(m.group(1)), int(m.group(2))
                if (groups, items) != (DOCTOR_GROUPS, DOCTOR_ITEMS):
                    problems.append(
                        f"{path}:{i} 声明值=doctor {groups} 组 / {items} 项；"
                        f"实测值={DOCTOR_GROUPS} 组 / {DOCTOR_ITEMS} 项"
                        f"（行={ln.strip()[:90]}）"
                    )
        if hits == 0:
            problems.append(
                f"{path}:— 声明值=缺「doctor N 组 / N 项」；"
                f"实测值={DOCTOR_GROUPS} 组 / {DOCTOR_ITEMS} 项"
            )
    assert not problems, "\n".join(problems)


def test_bom_file_list_matches_disk():
    """D12：`tools/gate.py::_BOM_FILES` 必须 == 实际单 BOM 文件集合。

    单 BOM 是 G3 字节契约；`ruff format` 会剥离 BOM，故格式门禁按清单逐文件
    `--exclude`。清单腐烂（文件增删/加 BOM 未同步）会让豁免失效或放走漏网文件。
    """
    from tools.gate import _BOM, _BOM_FILES, _SKIP_DIRS, CODE_DIR

    actual: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(CODE_DIR):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            with path.open("rb") as fh:
                head = fh.read(6)
            if head[:3] == _BOM and head[3:6] != _BOM:
                actual.add(path.relative_to(CODE_DIR).as_posix())
    declared = set(_BOM_FILES)
    problems = []
    if len(_BOM_FILES) != len(declared):
        problems.append(f"tools/gate.py:— _BOM_FILES 有重复项：{_BOM_FILES}")
    if declared != actual:
        problems.append(
            "tools/gate.py:— 声明值（_BOM_FILES）"
            f"={sorted(declared)}；实测值（单 BOM 文件）={sorted(actual)}"
        )
    assert not problems, "\n".join(problems)


def test_db_audit_section_count_in_docs():
    """文档声明的 `db audit` 段数 == 5。"""
    paths = [
        DB_DOCS / "01-总览与定位.md",
        DB_DOCS / "04-领域模型与核心接口.md",
        DB_DOCS / "06-核心流程与关键时序.md",
        OPS_README,
    ]
    problems: list[str] = []
    for path in paths:
        hits = 0
        for i, ln in enumerate(_lines(path), 1):
            for m in _AUDIT_PAT.finditer(ln):
                hits += 1
                got = int(m.group(1))
                if got != AUDIT_SECTIONS:
                    problems.append(
                        f"{path}:{i} 声明值=db audit {got} 段；"
                        f"实测值={AUDIT_SECTIONS} 段"
                        f"（行={ln.strip()[:90]}）"
                    )
        if hits == 0:
            problems.append(f"{path}:— 声明值=缺「db audit N 段」；实测值={AUDIT_SECTIONS} 段")
    assert not problems, "\n".join(problems)
