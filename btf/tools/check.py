# -*- coding: utf-8 -*-
"""bt check —— 本地架构守护检查（阶段 0 任务 0.2；M1 4.4 六项 → 批 8 八项 → EX-4 九项）。

组合九项检查（对应架构纪律）：
    1. ruff lint        代码规范 + 随机源禁令（08 §13.4）
    2. lint-imports     import-linter 六铁律（03 §7.3 黑名单式）
    3. pytest smoke     冒烟：中文路径/环境自检（06 §11.3）
    4. deps_budget      依赖预算 ≤8（07 §12.11）
    5. package import   btf 可导入（骨架完整性）
    6. domain 白名单    btf/domain 仅标准库白名单（03 §7.3 铁律 3 白名单式，
                       M1 4.4；插件边界基线另跑 tools/check_plugin_boundary.py）
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

用法: python tools/check.py   （任意工作目录；用绝对解释器路径调用）
      bt check                （**同一脚本的 CLI 收编入口**，退出码一致：[btf/cli/main.py
                               的 `_cmd_check` → `btf.app.run_gate_check` → 本脚本]，
                               判定逻辑只有这一份——CLI 审查报告-20260930 P0）
退出码: 0=全绿 / 1=有失败项
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# importlinter 的 -m 入口在 Windows 下静默失败；用 venv Scripts 的入口脚本
LINT_IMPORTS = Path(PY).parent / "lint-imports.exe"

# Windows GBK 控制台防护（06 §11.3 部署要点 3：UTF-8 全链路）
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def run(name: str, cmd: list[str]) -> bool:
    print(f"\n=== {name} ===")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=False)
    ok = r.returncode == 0
    print(f"--- {name}: {'PASS' if ok else 'FAIL'} ---")
    return ok


def main() -> int:
    results = [
        run("1/9 ruff lint", [PY, "-m", "ruff", "check", "btf", "tests", "tools"]),
        run("2/9 import-linter 架构铁律", [str(LINT_IMPORTS)]),
        run("3/9 冒烟测试（中文路径/环境）",
            [PY, "-m", "pytest", "tests", "-m", "smoke", "-v"]),
        run("4/9 依赖预算", [PY, "tools/deps_budget.py"]),
        run("5/9 包可导入", [PY, "-c", "import btf; print('btf', btf.__version__)"]),
        run("6/9 domain 白名单", [PY, "tools/check_domain_whitelist.py"]),
        run("7/9 报告渲染级结构（新 14）", [PY, "tools/check_report_render.py"]),
        run("8/9 数据不变量校验（CC-6 + 负向对照）",
            [PY, "tools/check_data_quality.py"]),
        run("9/9 IO 单一入口（EX-4 完全体）+ 变异性自检",
            [PY, "tools/check_io_boundary.py", "--self-test"]),
    ]
    n_pass = sum(results)
    print(f"\n{'=' * 40}\nbt check: {n_pass}/9 通过")
    return 0 if n_pass == 9 else 1


if __name__ == "__main__":
    sys.exit(main())
