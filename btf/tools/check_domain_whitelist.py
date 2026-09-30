# -*- coding: utf-8 -*-
"""domain 白名单检查（03 §7.3 铁律 3 白名单式补强；M1 任务 4.4）。

import-linter forbidden 契约是黑名单式（列举已知第三方）；本脚本是其
对偶——**白名单**穷举允许项：btf/domain 只许 import 标准库白名单
（__future__/dataclasses/enum/datetime/collections.abc）与域内模块，
其余（含任何第三方、任何 btf 外层包、相对域外导入）一律违例。

用法：python tools/check_domain_whitelist.py（违例 → 逐条列出，exit 1）
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 允许的标准库模块（root 或其子模块）。
#: ⚠ 2026-09-28（批 8）两处订正——工具原表**漏项**，与 pyproject 契约 3
#: 的注释白名单（"typing/dataclasses/enum/datetime/__future__"）不符：
#:   · **补 `typing`**：契约注释明列，工具却未收录 → `btf/domain/cache.py`
#:     的 `typing.Any` 被误报（声明 vs 实现不符，第 13 处）；
#:   · **`collections` 由「仅 abc」放宽为整包**：`BoundedDict` 需要
#:     `OrderedDict`——标准库容器与 `dataclasses` 同级的中性依赖。
#: 铁律 3 的**黑名单契约**（import-linter）仍拦全部第三方包，本表只是其白
#: 名单对偶，故两项订正均不削弱"domain 零第三方"本义。
WHITELIST = {"__future__", "typing", "dataclasses", "enum", "datetime",
             "collections"}


def _allowed(mod: str) -> bool:
    if any(mod == w or mod.startswith(w + ".") for w in WHITELIST):
        return True
    return mod == "btf.domain" or mod.startswith("btf.domain.")


def check() -> list[str]:
    problems: list[str] = []
    for py in sorted((ROOT / "btf" / "domain").rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:                     # 相对导入：域内合法（level=1）
                    continue
                mod = node.module or ""
                if mod and not _allowed(mod):
                    problems.append(f"{py.relative_to(ROOT)}: from {mod} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if not _allowed(alias.name):
                        problems.append(
                            f"{py.relative_to(ROOT)}: import {alias.name}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("[fail] domain 白名单违例（03 §7.3 铁律 3）：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("[ok] domain 白名单检查通过（btf/domain 仅依赖："
          + ", ".join(sorted(WHITELIST)) + "）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
