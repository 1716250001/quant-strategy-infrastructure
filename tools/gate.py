# -*- coding: utf-8 -*-
"""tools/gate.py — 提交前门禁总入口（W6-DEP-01）

用法：
    python tools/gate.py            # 全跑，任一失败即非零退出
    python tools/gate.py --fast     # 跳过 pytest（只跑 ruff + config-check + help）

顺序（成本从低到高，先便宜的先失败）：
    1. ruff check          静态
    2. main.py --help      CLI 可构造
    3. main.py config-check 配置完整性
    4. pytest tests/       smoke（--fast 跳过）
"""
import os
import subprocess
import sys

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(cmd):
    """执行一条门禁命令，返回 (ok, 合并输出)。"""
    r = subprocess.run(cmd, cwd=CODE_DIR, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def _step(idx, total, label, cmd):
    ok, out = _run(cmd)
    print(f"[{idx}/{total}] {label} … {'PASS' if ok else 'FAIL'}")
    if not ok:
        print(out.rstrip())
        print(f"GATE: 第 {idx} 道失败，终止")
        sys.exit(1)


def main():
    fast = "--fast" in sys.argv[1:]
    py = sys.executable
    main_py = os.path.join(CODE_DIR, "main.py")

    total = 3 if fast else 4
    _step(1, total, "ruff check", [py, "-m", "ruff", "check", "."])
    _step(2, total, "main.py --help", [py, main_py, "--help"])
    _step(3, total, "main.py config-check", [py, main_py, "config-check"])
    if not fast:
        _step(4, total, "pytest tests/", [py, "-m", "pytest", "tests/", "-q"])

    print("GATE: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
