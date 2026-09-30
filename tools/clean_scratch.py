# -*- coding: utf-8 -*-
"""
tools/clean_scratch.py — `_scratch/` 定期清理（一次性探针/临时产物的寿命管理）
============================================================================
背景（《待完成需求.md》E1 遗留）：`_scratch/` 长期无固定清理机制，探针与测试产物积压
（2026-09-30 清点 = 70 项，最早 09-25）。本脚本给出"何时清、谁清"的固定答案。

规则（2026-09-30 立）:
  1. 寿命：默认 **7 天**（按 mtime），到期即删；`--days N` 可调。
  2. 谁清 / 何时清：每次交付批次收尾时，由当班执行者（CodeBuddy / 赤潮）跑一次本脚本。
     "探针当天用完当天删"仍是首选纪律，本脚本是**兜底**，不替代纪律。
  3. 保护区（永不删）：`wind-研究暂存/`（Wind 硬编码 key，10 文件；`.gitignore` 已忽略）——
     另有 `--keep` 可追加名字/前缀豁免（可多次）。
  4. 默认 **干跑**：只打印清单；`--apply` 才真删。

用法:
  python tools/clean_scratch.py                     # 干跑：看 7 天窗会删哪些
  python tools/clean_scratch.py --days 3            # 干跑：3 天窗
  python tools/clean_scratch.py --days 3 --apply    # 真删
  python tools/clean_scratch.py --keep r9_runs      # 额外豁免某名字/前缀

安全设计:
  1. 只动 `_scratch/` 顶层；每个待删路径 resolve 后必须仍位于 `_scratch/` 内，否则中止
  2. `_scratch/` 自身、保护区、未到期项：只报不动（跳过计数与可删计数分开对账）
  3. --apply 后复读目录，逐项核对"已消失"，任一残留即非零退出
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

SCRATCH = Path(__file__).resolve().parents[1] / "_scratch"

#: 保护区：名字（顶层项）——永不删，只统计
PROTECTED = {"wind-研究暂存"}


def entry_size(p: Path) -> int:
    """文件=字节数；目录=递归字节数（汇总失败时记 0，不影响删除判定）"""
    if p.is_file():
        return p.stat().st_size
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def main() -> int:
    ap = argparse.ArgumentParser(description="_scratch 定期清理（默认干跑）")
    ap.add_argument("--days", type=float, default=7.0, help="寿命窗(天)，默认 7")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认只报告）")
    ap.add_argument("--keep", action="append", default=[], help="额外豁免的名字或前缀（可多次）")
    args = ap.parse_args()

    if not SCRATCH.is_dir():
        print(f"[中止] _scratch 不存在：{SCRATCH}")
        return 1

    now = time.time()
    rows = []
    for p in sorted(SCRATCH.iterdir(), key=lambda x: x.stat().st_mtime):
        age = (now - p.stat().st_mtime) / 86400.0
        protected = p.name in PROTECTED or any(p.name == k or p.name.startswith(k) for k in args.keep)
        if protected:
            act = "KEEP(保护)"
        elif age >= args.days:
            act = "DELETE"
        else:
            act = "keep(未到期)"
        rows.append((age, p, act, entry_size(p)))

    deletable = [r for r in rows if r[2] == "DELETE"]
    print(f"_scratch = {SCRATCH}")
    print(f"寿命窗 = {args.days:g} 天 | 顶层项 {len(rows)} | 可删 {len(deletable)} "
          f"| 保护区 {sum(1 for r in rows if r[2].startswith('KEEP'))} "
          f"| 未到期 {sum(1 for r in rows if r[2].startswith('keep'))}")
    print("-" * 78)
    for age, p, act, size in rows:
        print(f"{age:6.1f}d  {act:<12s} {'DIR ' if p.is_dir() else 'FILE'} {p.name}  ({human(size)})")
    if not deletable:
        print("-" * 78)
        print("无可删项（干跑/真删均无动作）。")
        return 0

    if not args.apply:
        print("-" * 78)
        print(f"[干跑] 上述 {len(deletable)} 项将在 --apply 时删除"
              f"（合计 {human(sum(r[3] for r in deletable))}）。加 --apply 执行。")
        return 0

    print("-" * 78)
    failed = []
    for _, p, _, _ in deletable:
        if SCRATCH.resolve() not in p.resolve().parents:
            print(f"[中止] 越界路径：{p}")
            return 1
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            print(f"已删 {p.name}")
        except OSError as e:
            failed.append((p.name, str(e)))
            print(f"[失败] {p.name}: {e}")

    left = {p.name for p in SCRATCH.iterdir()}
    residual = [r[1].name for r in deletable if r[1].name in left]
    print(f"复读校验: 删除 {len(deletable) - len(residual)}/{len(deletable)}"
          f"{' PASS' if not residual else ' FAIL ' + str(residual)}")
    return 1 if (residual or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
