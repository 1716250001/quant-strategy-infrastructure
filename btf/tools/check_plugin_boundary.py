# -*- coding: utf-8 -*-
"""插件边界检查（04 §8.5「新增任何扩展 = 新文件 + 配置注册；核心包 diff
必须为零」；M1 任务 4.4——扩展工作流的"扩展目录外修改"守卫）。

两个子检查：
    A. 注册点唯一性（每次运行）：``register(...)`` 程序化登记只允许出现在
       tests/、examples/ 与启动器脚本——btf 核心包（registry 除外）出现
       程序化注册即违例（扩展登记必须走配置声明或启动期注入点）。
    B. 核心基线（哈希快照）：记录核心包文件 SHA256（tools/.plugin_baseline.json）；
       扩展工作流中基线漂移 = 核心被改动 → 违例。--update 刷新基线
       （仅限核心自身的开发变更，配合 ADR/评审）。

    C. **基线刷新留痕（AA-3，19 号 §30.8.3 / §31.3）**：`--update` 时把
       「哪些文件变更、旧→新哈希、依据（--reason）、时间」追加到
       `tools/.plugin_baseline_history.jsonl`，并打印 CHANGELOG 建议行。
       为什么：本项目非 git 仓库，"核心 diff 为零"的唯一凭据就是该快照——
       若"重新定基"本身无留痕，就无法区分「合法随之刷新」与「用刷新掩盖
       漂移」（P3-NEW-2：这是"延后建 git"的具体代价实例）。

用法：
    python tools/check_plugin_boundary.py            # 检查（基线缺失时自动建立）
    python tools/check_plugin_boundary.py --update   # 开发变更后刷新基线
    python tools/check_plugin_boundary.py --update --reason "Z-1 报告转义修复 ..."
注：**不在** `bt check` 九项内（核心文件变更后由人按 AA-3 显式刷新留痕，
    故不并入常跑门禁——并入会让"基线漂移"被自动刷新掩盖；单跑本脚本）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / ".plugin_baseline.json"
#: 基线刷新历史（AA-3）：每行一条 JSON（时间 / 依据 / 变更文件旧→新哈希）
BASELINE_HISTORY = ROOT / "tools" / ".plugin_baseline_history.jsonl"

#: 核心包（扩展工作流中 diff 必须为零）；registry 名字表 = 合法登记点
#: `btf/viz` 于 2026-09-28（批 7）补入——原列表遗漏报告层，致 `viz/report.py`
#: 的多批改动**未被快照覆盖**（基线"核心 diff 为零"在报告层不可证）。
#: `btf/runtime` 于 2026-09-29（R4 第一刀）由**单文件**升为**包**（`runtime.py`
#: → `runtime/_runtime.py` + 4 个协作模块）⇒ 由 CORE_FILES 改入 CORE 整体跟踪
#: （否则拆分后的新文件不进快照，"核心 diff 为零"在装配根不可证）。
CORE = [
    "btf/domain", "btf/engine", "btf/execution", "btf/risk",
    "btf/portfolio", "btf/strategy", "btf/data", "btf/analytics",
    "btf/experiment", "btf/config", "btf/cli", "btf/viz", "btf/runtime",
]
CORE_FILES = ["btf/__init__.py"]

#: 允许出现程序化 register() 的目录（测试注入/示例/工具）
REGISTER_OK_PREFIXES = ("tests", "examples", "tools")


def _find_register_calls(path: Path) -> list[str]:
    hits: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return [f"{path.relative_to(ROOT)}: 语法错误（无法扫描）"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name) else "")
            if name == "register":
                hits.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return hits


def check_register_points() -> list[str]:
    problems: list[str] = []
    for py in sorted(ROOT.glob("btf/**/*.py")):
        rel = py.relative_to(ROOT).as_posix()
        if rel.startswith("btf/registry/"):
            continue                     # 名字表本体 = 唯一内置登记点
        for hit in _find_register_calls(py):
            problems.append(
                f"核心包出现程序化注册 {hit}——扩展登记必须走配置声明"
                f"（registry catalog）或启动期注入（tests/examples）")
    return problems


def _core_hashes() -> dict[str, str]:
    out: dict[str, str] = {}
    for d in CORE:
        for py in sorted((ROOT / d).rglob("*.py")):
            out[py.relative_to(ROOT).as_posix()] = hashlib.sha256(
                py.read_bytes()).hexdigest()
    for f in CORE_FILES:
        p = ROOT / f
        if p.exists():
            out[f] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _record_history(changed: dict[str, dict[str, str | None]],
                    reason: str, created: bool) -> Path:
    """追加基线刷新留痕（AA-3；返回历史文件路径）。"""
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason or "(未注明依据——请用 --reason 补记)",
        "kind": "create" if created else "update",
        "changed_files": sorted(changed),
        "changed": changed,
    }
    with BASELINE_HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return BASELINE_HISTORY


def check_baseline(update: bool, reason: str = ""
                   ) -> tuple[list[str], bool, dict[str, dict[str, str | None]],
                              bool]:
    """检查/刷新基线。返回 `(problems, updated, changed, created)`。

    `changed` = `{文件: {old, new}}`（AA-3 留痕用；`created=True` 表首建基线）。
    """
    current = _core_hashes()
    if update or not BASELINE.exists():
        created = not BASELINE.exists()
        old = {} if created else json.loads(BASELINE.read_text(encoding="utf-8"))
        changed = {f: {"old": old.get(f), "new": current.get(f)}
                   for f in sorted(set(old) | set(current))
                   if old.get(f) != current.get(f)}
        BASELINE.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return [], update, changed, created
    old = json.loads(BASELINE.read_text(encoding="utf-8"))
    problems = []
    for f in sorted(set(old) | set(current)):
        if old.get(f) != current.get(f):
            problems.append(
                f"{f}: {'新增/修改' if f in current else '删除'}"
                f"（核心包 diff 非零——扩展工作流违例；核心自身变更请 --update 并走评审）")
    return problems, False, {}, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="插件边界检查（04 §8.5）")
    parser.add_argument("--update", action="store_true",
                        help="刷新核心基线（核心自身开发变更后使用）")
    parser.add_argument("--reason", default="",
                        help="刷新依据（AA-3 留痕；建议写明批次/项号，如 "
                             "'Z-1 报告转义修复'）")
    args = parser.parse_args(argv)

    problems = check_register_points()
    if problems:
        # 注册点违例优先失败：不把违例态写入基线（--update 不短路本检查）
        print("[fail] 插件边界违例：")
        for p in problems:
            print(f"  - {p}")
        return 1

    baseline_problems, updated, changed, created = check_baseline(
        args.update, args.reason)
    if updated:
        print(f"[ok] 核心基线已刷新（{len(_core_hashes())} 个文件）"
              f" → tools/.plugin_baseline.json")
        if changed:
            history = _record_history(changed, args.reason, created=created)
            print(f"[留痕] 变更 {len(changed)} 文件（AA-3）→ "
                  f"{history.relative_to(ROOT).as_posix()}")
            for f, h in list(changed.items())[:12]:
                old_s = (h['old'] or '—')[:8]
                new_s = (h['new'] or '—')[:8]
                print(f"  - {f}  {old_s} → {new_s}")
            if len(changed) > 12:
                print(f"  …（其余 {len(changed) - 12} 项见历史文件）")
            print("[CHANGELOG 建议行] 基线刷新：核心 "
                  f"{len(changed)} 文件（{', '.join(sorted(changed)[:6])}"
                  f"{' …' if len(changed) > 6 else ''}）"
                  f"；依据={args.reason or '(未注明)'}")
        else:
            print("[留痕] 核心基线无变更（未写历史；AA-3）")
        return 0
    if baseline_problems:
        print("[fail] 插件边界违例（核心基线漂移）：")
        for p in baseline_problems:
            print(f"  - {p}")
        return 1
    print("[ok] 插件边界检查通过（注册点唯一 + 核心基线一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
