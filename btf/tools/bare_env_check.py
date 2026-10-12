# -*- coding: utf-8 -*-
"""**C-01（三轮架构审查）**：btf 独立环境「裸验证」——把"依赖预算 ≤8"从
文档层约束变成**环境层实测**。

要解决的问题
------------
`btf/requirements.txt` 写明"依赖预算 ≤8（架构 07 §12.11）"，但双工程**共享**
同一个 venv（`代码\\_venv`）⇒ 环境层无法强制：任何第三方包（手工 `pip install`
的调试残留、间接依赖、共享 venv 里的既有包）都能让 btf 悄悄"吃到"预算外的包，
而本机永远测不出来。**实证代价**：把 btf 分享给独立环境首次运行即暴露
（导入成功 ≠ 依赖已声明）。

做法（与 CI 容器等价的本地形态）
--------------------------------
在**临时独立 venv** 里重建环境，只装**声明过的依赖**，再跑真入口与测试子集：

  ① `python -m venv <tmp>`                       （全新解释器，无共享残留）
  ② `pip install -r 代码/requirements.txt`        （底座：部署视图全量冻结）
  ③ `pip install -r btf/requirements.txt`         （btf 新增依赖；预算 ≤8）
  ④ `<tmp>/python -m btf.cli.main --version`      （**真入口**——NOTE-4 防复发：
                                                     `_version.py` 单源必须真被读到）
  ⑤ `<tmp>/python -m pytest <子集> -q`            （默认 tests/unit：L1/L2 层）

退出码：0＝通过；1＝失败；2＝用法/前置缺失（**不静默通过**）。

用法
----
    python tools/bare_env_check.py --plan        # 只打印将执行的步骤（零副作用）
    python tools/bare_env_check.py --run         # 真正建环境并验证（需网络；数分钟）
    python tools/bare_env_check.py --run --keep  # 保留临时环境（排查用）

节奏建议：**每季度一次**或"新增依赖/改动 requirements.txt 之后"必跑
（写入 `btf/tools/README.md` 与 20 号指引；不自动进 `bt check`——它要建 venv、
要联网，不属于"秒级常跑门禁"）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BTF = Path(__file__).resolve().parents[1]          # btf/
CODE = BTF.parent                                  # 代码/
BASE_REQ = CODE / "requirements.txt"
BTF_REQ = BTF / "requirements.txt"
MIN_BASE_FALLBACK = ("pandas", "numpy", "pyarrow", "tushare", "akshare",
                     "scipy", "requests", "pyyaml")   # 仅当底座清单缺失时用


def _harden_stdout() -> None:
    """GBK 控制台下符号会让 print 抛错（同 doctor 教训）。"""
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass


def _plan(steps: list[str]) -> None:
    print("将执行的步骤（--plan：零副作用）:")
    for i, s in enumerate(steps, 1):
        print(f"  {i}. {s}")


def _run(cmd: list[str], cwd: Path | None = None) -> int:
    print(f"\n$ {' '.join(cmd)}")
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    tail = (p.stdout or "").strip().splitlines()[-8:]
    err = (p.stderr or "").strip().splitlines()[-8:]
    for ln in tail:
        print(f"  | {ln}")
    if p.returncode != 0 and err:
        print("  stderr:")
        for ln in err:
            print(f"  ! {ln}")
    return p.returncode


def main(argv=None) -> int:
    _harden_stdout()
    ap = argparse.ArgumentParser(
        description="btf 独立环境裸验证（C-01）：临时 venv 重建 + 真入口 + 测试子集")
    ap.add_argument("--plan", action="store_true", help="只打印步骤（默认行为）")
    ap.add_argument("--run", action="store_true", help="真正执行（需网络；数分钟）")
    ap.add_argument("--keep", action="store_true", help="保留临时环境目录")
    ap.add_argument("--tests", default="tests/unit",
                    help="pytest 子集（默认 tests/unit；`-` = 跳过测试）")
    ap.add_argument("--python", default=sys.executable, help="建 venv 用的解释器")
    a = ap.parse_args(argv)

    base_missing = not BASE_REQ.is_file()
    if base_missing:
        print(f"[WARN] 底座依赖清单不存在: {BASE_REQ} → 退化为最小底座集合")
    steps = [
        f"{a.python} -m venv <tmp>/venv",
        (f"<tmp>/venv/…/python -m pip install -r {BASE_REQ}" if not base_missing
         else "<tmp>/venv/…/python -m pip install " + " ".join(MIN_BASE_FALLBACK)),
        f"<tmp>/venv/…/python -m pip install -r {BTF_REQ}",
        "<tmp>/venv/…/python -m btf.cli.main --version",
        (f"<tmp>/venv/…/python -m pytest {a.tests} -q（cwd=btf/）"
         if a.tests != "-" else "（跳过测试子集）"),
    ]
    if not a.run:
        _plan(steps)
        print("\n[提示] 加 --run 真正执行（建议每季度或改动 requirements 后跑一次）")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="btf_bare_"))
    venv = tmp / "venv"
    py = venv / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    rc = 0
    try:
        if _run([a.python, "-m", "venv", str(venv)]) != 0:
            return 1
        if base_missing:
            rc |= _run([str(py), "-m", "pip", "install", *MIN_BASE_FALLBACK])
        else:
            rc |= _run([str(py), "-m", "pip", "install", "-r", str(BASE_REQ)])
        rc |= _run([str(py), "-m", "pip", "install", "-r", str(BTF_REQ)])
        if rc == 0:
            # 真入口（NOTE-4：`bt --version` 必须由**真入口**报版本）
            rc |= _run([str(py), "-m", "btf.cli.main", "--version"], cwd=BTF)
            if rc == 0 and a.tests != "-":
                rc |= _run([str(py), "-m", "pytest", *a.tests.split(), "-q"],
                           cwd=BTF)
        print("\n裸验证结论:", "通过（独立环境可重建、真入口可跑）" if rc == 0
              else f"失败（退出码 {rc}）——见上方输出")
        return 0 if rc == 0 else 1
    finally:
        if a.keep:
            print(f"（保留临时环境: {tmp}）")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
