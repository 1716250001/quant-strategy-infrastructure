# -*- coding: utf-8 -*-
"""tools/gate.py — 提交前门禁总入口（W6-DEP-01）

用法：
    python tools/gate.py            # 全跑，任一失败即非零退出
    python tools/gate.py --fast     # 跳过 pytest（只跑 ruff + config-check + help）

顺序（成本从低到高，先便宜的先失败）：
    1. 双 BOM 扫描          源头字节（D9 硬条件，必须**先于** ruff）
    2. ruff check          静态
    3. ruff format --check 格式（D11 归零；与 check 分离，失败提示"ruff format ."）
    4. main.py --help      CLI 可构造
    5. main.py config-check 配置完整性
    6. pytest tests/       smoke（--fast 跳过）
"""

import os
import subprocess
import sys

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BOM = b"\xef\xbb\xbf"
_SKIP_DIRS = {
    ".git",
    ".venv",
    "_venv",
    "__pycache__",
    "_scratch",
    ".temp",
    "build",
    "dist",
    ".ruff_cache",
    ".pytest_cache",
}

#: **单 BOM 文件**（G3 字节契约：头部恰 1 个 UTF-8 BOM）。
#: `ruff format` 会把 BOM 剥离（实测 0.16.9：写回时丢 BOM），故格式门禁对这
#: 11 个文件**豁免检查**——否则"格式化"与"BOM 契约"互相打架。双 BOM 扫描
#: （第 1 道）仍覆盖全部文件；BOM 文件增减须同步本清单。
_BOM_FILES = (
    "alt/audit.py",
    "alt/reader.py",
    "alt/update.py",
    "common/calendar.py",
    "fetch/daily_update.py",
    "tools/doctor.py",
    "tools/db/schema.py",
    "tools/research/build_micro_index.py",
    "tools/research/factor_library.py",
    "tools/research/fund_pool_builder.py",
    "tools/research/manager_profile.py",
)


def _format_check_cmd(py: str) -> list[str]:
    """格式门禁命令（BOM 文件豁免——`ruff format` 会剥离 BOM）。"""
    cmd = [py, "-m", "ruff", "format", "--check", "."]
    for rel in _BOM_FILES:
        cmd += ["--exclude", rel]
    return cmd


def _double_bom_files() -> list[str]:
    """全仓 `.py` 头部 **≥2 个** UTF-8 BOM 的文件（相对 CODE_DIR）。

    G3/B2 事故根因是外部进程反复给三个源文件叠加 BOM；ruff 只报
    `invalid-syntax` 而不点名字节层原因。本扫描置于 ruff 之前，复发即红。
    """
    bad: list[str] = []
    for dirpath, dirnames, filenames in os.walk(CODE_DIR):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "rb") as fh:
                    head = fh.read(len(_BOM) * 2)
            except OSError:
                continue
            if head == _BOM * 2:
                bad.append(os.path.relpath(path, CODE_DIR))
    return sorted(bad)


def _run(cmd):
    """执行一条门禁命令，返回 (ok, 合并输出)。"""
    r = subprocess.run(
        cmd, cwd=CODE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
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

    total = 5 if fast else 6
    print(f"[1/{total}] 双 BOM 扫描 … ", end="")
    bad = _double_bom_files()
    if bad:
        print("FAIL")
        print("以下文件头部含 ≥2 个 UTF-8 BOM（须规范为 1 个）：")
        for rel in bad:
            print(f"  - {rel}")
        print("GATE: 第 1 道失败，终止")
        sys.exit(1)
    print("PASS")
    _step(2, total, "ruff check", [py, "-m", "ruff", "check", "."])
    _step(3, total, "ruff format --check", _format_check_cmd(py))
    _step(4, total, "main.py --help", [py, main_py, "--help"])
    _step(5, total, "main.py config-check", [py, main_py, "config-check"])
    if not fast:
        _step(6, total, "pytest tests/", [py, "-m", "pytest", "tests/", "-q"])

    print("GATE: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
