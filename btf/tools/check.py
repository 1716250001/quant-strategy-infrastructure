# -*- coding: utf-8 -*-
"""bt check —— 本地架构守护检查（阶段 0 任务 0.2；门禁项数演进：M1 4.4 六项 → 批 8 八项 → EX-4 9 项 → **GG-6② 十项**）。

组合十项检查（对应架构纪律）：
    1. ruff lint        代码规范 + 随机源禁令（08 §13.4）
    2. lint-imports     import-linter 六铁律（03 §7.3 黑名单式）
    3. pytest smoke     冒烟：中文路径/环境自检（06 §11.3）
    4. deps_budget      依赖预算 ≤8（07 §12.11）
    5. package import   btf 可导入（骨架完整性）
    6. domain 白名单    btf/domain 仅标准库白名单（03 §7.3 铁律 3 白名单式，
                       M1 4.4；**插件边界基线自 GG-6② 起为第 10 项**）
    7. 报告渲染级结构   HTML 交付物**渲染后结构**机检（Z-5；19 号 §26.2 /
                       铁律新 14「渲染/序列化产物必须做渲染级断言」）——
                       治第六种逃逸模式：转义只动标签不动文本，文本级断言
                       对「正文整体被转义」结构性失明
    8. 数据不变量校验   CC-1..CC-5 常跑机检（批 8 §39.2 CC-6）：主库 20 标的
                       × 1 年正样本 **+ 负向对照**（注入 5 类缺陷必须全部检出；
                       铁律新 15）——治"校验了但没结果/从不触发"
    9. IO 单一入口      EX-4 完全体（19 号 §9.2 / §47.4 余项 ②）：`btf/` 内
                       **只有** `data/parquet_reader.py`（实现）+ `data/core.py`
                       （唯一消费者）可触磁盘——AST 扫描 + `--self-test` 变异性
                       自检（注入违例必检出、合规样本不误报）
   10. 插件边界基线     核心包 diff 必须为零（**GG-6②**，19 号 §63）：基线
                       `tools/.plugin_baseline.json` + 留痕
                       `.plugin_baseline_history.jsonl`（AA-3）；刷新必须显式
                       `--update --reason`。此前该门禁"另跑"不进门禁 ⇒
                       五批"门禁全绿"**未覆盖**契约 6（P1-NEW-13）。

用法: python tools/check.py   （任意工作目录；用绝对解释器路径调用）
      bt check                （**同一脚本的 CLI 收编入口**，退出码一致：[btf/cli/main.py
                               的 `_cmd_check` → `btf.app.run_gate_check` → 本脚本]，
                               判定逻辑只有这一份——CLI 审查报告-20260930 P0）
逐项输出落盘（**N4 加固**，2026-10-06）：默认 `代码/_scratch/bt_check_logs/<时间戳>/`
     （`BTFTOOL_CHECK_LOG_DIR` 可覆盖；每项每尝试一个 `.log`）；第 3 项**冷启动偶发假红**
     自动复跑一次并**显式标注**"首次失败/复跑通过"（不隐藏偶发；其余项失败＝真失败）。
     **N4 根因修复（2026-10-06，§66.10 收口批）**：第 3 项显式 `--basetemp` 指**项目内**
     `代码/_scratch/pytest_tmp/check`（`BTFTOOL_CHECK_PYTEST_BASETEMP` 可覆盖）——假红根因 =
     系统 `%TEMP%` 的 pytest `garbage-*` 累积超阈值触发环境 safe-delete 守卫（19 号 §67.10 三）。
     **N6 分根（2026-10-06；19 号 §69.3）**：本项 basetemp 用**独立子根** `…/pytest_tmp/check`
     （`bt test` 用 `…/pytest_tmp/bt_test`）——两通道残留**互不污染**（原共根时，任一残留
     超守卫阈值会让本项"清空根"被拦 ⇒ 假红）。**变量命名纪律（N7；19 号 §69.4）**：工具
     变量一律 `BTFTOOL_` 前缀——**禁用 `BTF_` 前缀**（会被配置层当覆盖键注入 ⇒ 配置校验失败）。
退出码: 0=全绿 / 1=有失败项
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# importlinter 的 -m 入口在 Windows 下静默失败；用 venv Scripts 的入口脚本
LINT_IMPORTS = Path(PY).parent / "lint-imports.exe"

# Windows GBK 控制台防护（06 §11.3 部署要点 3：UTF-8 全链路）
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# ── N4 加固（2026-10-06；19 号 §67.3 N4 / §67.9：冷启动偶发假红**两次独立复现**）──
#: ① 逐项输出**落盘**（`BTFTOOL_CHECK_LOG_DIR` 可覆盖；每次运行一个时间戳子目录）
#: —— 治"偶发假红无日志可归因"（上一轮两次假红均因无失败日志而无法定论）。
#: 变量名 `BTFTOOL_` 前缀（**N7**：`BTF_` 前缀＝配置覆盖命名空间 ⇒ 配置校验失败）。
LOG_DIR = Path(os.environ.get(
    "BTFTOOL_CHECK_LOG_DIR", ROOT.parent / "_scratch" / "bt_check_logs"))
RUN_STAMP = time.strftime("%Y%m%d-%H%M%S")
#: ③ **仅在"已知偶发"项上**允许自动复跑一次，且**显式标注**（"首次失败/复跑通过"）——
#: **不隐藏**偶发；未列此项的失败一律按真失败处置。
RETRY_ON_FIRST_FAIL = ("3/10 冒烟测试（中文路径/环境）",)
FLAKES_ANNOTATED: list[str] = []
#: ④ 第 3 项 pytest 显式 `--basetemp` 指**项目内独立子根**（**N4 根因修复** 2026-10-06；
#: **N6 分根** 2026-10-06，19 号 §69.3）：假红根因 = 系统 `%TEMP%\pytest-of-*` 的
#: `garbage-*` 累积超阈值（实测 82 > 50）⇒ 环境 safe-delete 守卫拦截 pytest 清理 ⇒
#: 非零退出。**独立子根** `…/pytest_tmp/check`（`bt test` 用 `…/pytest_tmp/bt_test`）
#: ⇒ 两通道残留互不污染（原共根时，任一残留 > 守卫阈值会让"清空根"被拦 ⇒ 假红）。
#: 变量名 `BTFTOOL_` 前缀（**N7**；19 号 §69.4）。
PYTEST_BASETEMP = Path(os.environ.get(
    "BTFTOOL_CHECK_PYTEST_BASETEMP",
    ROOT.parent / "_scratch" / "pytest_tmp" / "check"))


def _slug(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", name).strip("_")[:48]


def _exec(name: str, cmd: list[str], attempt: int) -> bool:
    """跑一项：**捕获输出**（打印 + 落盘，N4①）＋子进程显式 UTF-8（N4②，06 §11.3）。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    out = (r.stdout or "") + (r.stderr or "")
    sys.stdout.write(out)
    sys.stdout.flush()
    log = LOG_DIR / RUN_STAMP / f"{_slug(name)}.attempt{attempt}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        log.write_text(out, encoding="utf-8")
    except OSError as exc:                     # 日志失败不得掩盖门禁结论
        print(f"[warn] 日志写入失败（{exc}）——不影响本项判定")
    return r.returncode == 0


def run(name: str, cmd: list[str], *, retryable: bool = False) -> bool:
    print(f"\n=== {name} ===")
    ok = _exec(name, cmd, 1)
    if not ok and retryable:
        print(f"--- {name}: FAIL（首次）→ 自动复跑一次"
              f"（N4 冷启动偶发；两次输出均已留盘）---")
        if _exec(name, cmd, 2):
            FLAKES_ANNOTATED.append(name)
            print(f"--- {name}: PASS（**首次失败 / 复跑通过**"
                  f"——N4 冷启动偶发，已留痕）---")
            return True
        print(f"--- {name}: FAIL（复跑亦失败 ⇒ 按**真失败**处置）---")
        return False
    print(f"--- {name}: {'PASS' if ok else 'FAIL'} ---")
    return ok


def main() -> int:
    results = [
        run("1/10 ruff lint", [PY, "-m", "ruff", "check", "btf", "tests", "tools"]),
        run("2/10 import-linter 架构铁律", [str(LINT_IMPORTS)]),
        run("3/10 冒烟测试（中文路径/环境）",
            [PY, "-m", "pytest", "tests", "-m", "smoke", "-v",
             "--basetemp", str(PYTEST_BASETEMP)],
            retryable=True),                     # N4③：已知冷启动偶发项
        run("4/10 依赖预算", [PY, "tools/deps_budget.py"]),
        run("5/10 包可导入", [PY, "-c", "import btf; print('btf', btf.__version__)"]),
        run("6/10 domain 白名单", [PY, "tools/check_domain_whitelist.py"]),
        run("7/10 报告渲染级结构（新 14）", [PY, "tools/check_report_render.py"]),
        run("8/10 数据不变量校验（CC-6 + 负向对照）",
            [PY, "tools/check_data_quality.py"]),
        run("9/10 IO 单一入口（EX-4 完全体）+ 变异性自检",
            [PY, "tools/check_io_boundary.py", "--self-test"]),
        run("10/10 插件边界基线（契约 6；GG-6②）",
            [PY, "tools/check_plugin_boundary.py"]),
    ]
    n_pass = sum(results)
    print(f"\n{'=' * 40}\nbt check: {n_pass}/10 通过")
    print(f"逐项日志（N4①）：{LOG_DIR / RUN_STAMP}")
    if FLAKES_ANNOTATED:
        print("⚠ 冷启动偶发（N4③，**不隐藏**）："
              + "；".join(f"{n}（首次失败/复跑通过）" for n in FLAKES_ANNOTATED))
    if n_pass != 10:
        print("失败项请附**逐项日志**原样回执（19 号 §67.7 硬约束 5）")
    return 0 if n_pass == 10 else 1


if __name__ == "__main__":
    sys.exit(main())
