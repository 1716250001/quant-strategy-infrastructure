# -*- coding: utf-8 -*-
"""依赖预算计数器（架构 07 §12.11：探索期新直接依赖 ≤8）。

用法: python tools/deps_budget.py            （= `bt check` 第 4/9 项；单跑仅用于定位）
退出码: 0=预算内 / 2=超预算
来源：pyproject.toml（tomllib 解析，Python 3.11+ 标准库）。
口径：[project].dependencies + [project.optional-dependencies] 全部组，
同名包跨组只计一次（预算按"新引入的包"算）；开发工具链（ruff 等
仅开发机使用、不入 pyproject 依赖者）不计入——架构 07 §12.11 原清单口径。
"""
import sys
import tomllib
from pathlib import Path

BUDGET = 8  # 架构纪律：新直接依赖预算上限

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台防崩

    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    proj = data.get("project", {})

    deps: dict[str, str] = {}
    for dep in proj.get("dependencies", []):
        deps[dep.split(">")[0].split("<")[0].split("=")[0].split("!")[0].split("~")[0].split("[")[0].strip()] = "runtime"
    for group, items in proj.get("optional-dependencies", {}).items():
        for dep in items:
            name = dep.split(">")[0].split("<")[0].split("=")[0].split("!")[0].split("~")[0].split("[")[0].strip()
            deps.setdefault(name, group)

    print(f"btf 新直接依赖（去重）: {len(deps)}/{BUDGET}")
    for name, group in sorted(deps.items()):
        print(f"  - {name:20s} [{group}]")
    if len(deps) > BUDGET:
        print("超预算！架构 07 §12.11 纪律违规，需 ADR 评审")
        return 2
    print("预算内 [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
