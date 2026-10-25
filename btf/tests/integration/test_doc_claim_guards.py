# -*- coding: utf-8 -*-
"""L2：**文档披露数字守卫**（R60′，2026-10-09；19 号 §109.5/§110.2）。

四组守卫（**只读**文档；T-C 读 19 号作完成清单源头；**不写**任何文件；不碰业务代码）：

- **T-A 计数守卫**：六份现状文档必须**同时声明**两个口径（「tag 快照」＋「工作区」），
  且**「工作区」值 == 实测 collect 非 L5 数**（子进程 cwd ＝ `代码/btf`；`--collect-only`
  **不带 `-q`**——本环境带 `-q` 时按行计数恒为 0，见 B3 纪律）。
- **T-B 口径守卫**：**现状文档＋20 号**的 **warm-up 上下文**数字必须 == 夹具
  `QIDIAN_LOOKBACK_BARS`（MM-3：`QidianStrategy` 在仓外，本守卫**不 import 仓外包**；
  取值真源见 `tests/fixtures/strategy_contract.py`，与仓外 `test_contract_sync.py`
  双侧互校）；**19 号审查留痕不参与 T-B 数值校验**（O-1 补全，19 号 §156）；
  正则带 `warm.?up` 前缀锚（**禁裸扫数字**：文档另有「冷备 252 文件 / 3446 KB」
  「日频 `×√252`」）。
- **T-C 矛盾扫描器**：先从 **19 号**提取「已交付/验收通过/闭合」的单号集合，再扫其余
  文档：出现「**待 Rxx′**」且该单号已在集合内 ⇒ 失败（**源头单一取自 19 号**，不在测试里
  硬编码完成清单）。
- **T-D 时变量格式守卫**：CHANGELOG 的 digest 清单条目必须含「**扫描时刻**」＋日期；
  **不锁数值**（一日内 82→96→115 已实证为时变量）。

失败信息统一含「**文档路径＋行号＋声明值＋实测值**」（§110.2 要求）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.strategy_contract import QIDIAN_LOOKBACK_BARS

pytestmark = [pytest.mark.l2]

#: `tests/integration/x.py` ⇒ [0]=integration、[1]=tests、[2]=btf、[3]=代码（§110.2 固定定位）
_HERE = Path(__file__).resolve()
BTF_ROOT = _HERE.parents[2]
DOC_DIR = _HERE.parents[3] / "代码架构" / "回测工具架构"
CHANGELOG = BTF_ROOT / "CHANGELOG.md"

#: 被测「现状文档」组（T-A/T-B/T-C；19 号只作 T-C 的完成清单源头）
CURRENT_DOCS = (
    "README.md",
    "01-总览与定位.md",
    "07-技术选型建议与权衡.md",
    "09-测试验证与基准.md",
    "11-演进路线风险与PoC.md",
    "16-架构评审与验收标准.md",
)
USAGE_DOC = "20-回测平台使用指引.md"
DOC_19 = "19-架构审查与重构演进记录.md"


def _read(name: str) -> list[str]:
    return (DOC_DIR / name).read_text(encoding="utf-8").splitlines()


def _measured_non_l5() -> int:
    """实测 collect 非 L5 数（cwd＝`代码/btf`；`--collect-only` **不带 `-q`**）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only",
         "-p", "no:cacheprovider", "-m", "not l5"],
        cwd=str(BTF_ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0, (
        f"collect 失败（EXIT={proc.returncode}）：{proc.stdout[-400:]}")
    return sum(1 for ln in proc.stdout.splitlines() if "::" in ln)


# ───────────────────────── T-A 计数守卫 ─────────────────────────

def test_t_a_count_guard():
    """① 双口径**存在性**（tag 快照＋工作区）；② 工作区值 == 实测 collect。"""
    measured = _measured_non_l5()
    problems: list[str] = []
    for name in CURRENT_DOCS:
        lines = _read(name)
        seen = 0
        for i, ln in enumerate(lines, 1):
            if "工作区" not in ln:
                continue
            seen += 1
            if "tag 快照" not in ln:
                problems.append(
                    f"{DOC_DIR / name}:{i} 声明值=「工作区」缺配「tag 快照」双口径；"
                    f"实测值=工作区口径应为 {measured}（行={ln.strip()[:90]}）")
            if str(measured) not in ln:
                problems.append(
                    f"{DOC_DIR / name}:{i} 声明值（工作区）≠ 实测 {measured}；"
                    f"实测值={measured}（行={ln.strip()[:90]}）")
        if seen == 0:
            problems.append(
                f"{DOC_DIR / name}:—  声明值=缺「工作区」口径声明（双口径要求）；"
                f"实测值=工作区应为 {measured}")
    assert not problems, "\n".join(problems)


# ───────────────────────── T-B 口径守卫 ─────────────────────────

#: 仅带 `warm.?up` 前缀锚（禁裸扫）；**两处 `(?<![_\w])`**：前者防**标识符**误伤
#: （`test_t_b_warmup_value_guard`／`walkforward_warmup_n60.py`——`warm` 前是 `_`/字母
#: ⇒ 非「warm-up 上下文」），后者防 `warmup_n60` 这类后缀；**R66′ 补 `.`**（数字前紧跟
#: 小数点/小节号 ⇒ 非热身值：`§十一.4`、`v1.2.4` 这类**编号小数**曾被误捕——审查方
#: §125 行「warmup…§十一.4 基准 run」实证假阳性，阻塞发版回归；守卫「禁裸扫」意图
#: 不变）；`(?!\s*(?:-|→|~|—)\s*\d)` 防把**日期/区间**（`warm-up） | 2022-09-01`、
#: `（0 → 2 笔）`）当数字；`(?!\s*(?:笔|%|倍))` 防把**成交笔数/百分比**当数字。
_WARMUP_PAT = re.compile(
    r"(?<![_\w])warm[-\s]?up[^\d\n]{0,48}?(?<![_\w.])(\d{1,4})(?!\d)"
    r"(?!\s*(?:-|→|~|—)\s*\d)(?!\s*(?:笔|%|倍))")


def _warmup_number_ok(num: int, line: str, declared: int) -> bool:
    """只接受：声明值／`--warmup 0`（旧行为对照开关）。

    **O-1 补全（19 号 §156）**：19 号审查留痕不再参与 T-B 数值校验——本函数无
    `strict` 形参、无豁免词表；现状文档写错值即红。
    """
    if num == declared:
        return True
    return num == 0 and "--warmup" in line


def _tb_warmup_problems() -> list[str]:
    """T-B 检查主体：扫「现状文档＋20 号」，返回问题清单（空＝通过）。"""
    declared = QIDIAN_LOOKBACK_BARS
    problems: list[str] = []
    for name in (*CURRENT_DOCS, USAGE_DOC):
        for i, ln in enumerate(_read(name), 1):
            for m in _WARMUP_PAT.finditer(ln):
                num = int(m.group(1))
                if not _warmup_number_ok(num, ln, declared):
                    problems.append(
                        f"{DOC_DIR / name}:{i} 声明值=warm-up {num}；"
                        f"实测值=声明 {declared}（行={ln.strip()[:90]}）")
    return problems


def test_t_b_warmup_value_guard():
    """现状文档 warm-up 上下文数字 == 夹具 `QIDIAN_LOOKBACK_BARS` 声明值（MM-3）。

    MM-3（19 号 §135.2）前此守卫经仓外包 import 取值——包名撞车后沦为全量
    唯一 skip（守卫空转）；现改读 `tests/fixtures/strategy_contract.py`，
    与仓外 `cc_strategies/tests/test_contract_sync.py` 双侧互校：策略实现
    漂移 ⇒ 仓外侧红；夹具/文档漂移 ⇒ 本守卫红（双向可证伪）。

    **O-1 补全（19 号 §156）**：19 号审查留痕已从本守卫移除，不再需要豁免词表。
    """
    problems = _tb_warmup_problems()
    assert not problems, "\n".join(problems)


def test_tb_catches_wrong_warmup_in_current_doc(tmp_path, monkeypatch):
    """T-B 必须能红：现状文档副本写错 warmup ⇒ 断言失败（19 号 §156 C）。"""
    for name in (*CURRENT_DOCS, USAGE_DOC):
        shutil.copy2(DOC_DIR / name, tmp_path / name)
    bad = tmp_path / "09-测试验证与基准.md"
    bad.write_text(
        bad.read_text(encoding="utf-8")
        + "\n> 反向测试注入：warmup=999（故意写错）\n",
        encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "DOC_DIR", tmp_path)

    problems = _tb_warmup_problems()
    print("\n[reverse] 注入 warmup=999 ⇒ T-B 问题：\n" + "\n".join(problems))
    assert problems, "T-B 未检出错误 warm-up 值（守卫失效）"

    with pytest.raises(AssertionError) as ei:
        test_t_b_warmup_value_guard()
    print("\n[reverse] T-B 原始断言失败（预期）：\n" + str(ei.value))


# ───────────────────────── T-C 矛盾扫描器 ─────────────────────────

#: 「已完成」语义标记（取自 19 号正文；**清单不硬编码**，逐行提取）
_DONE_MARKERS = ("验收通过", "交付合格", "判为交付", "已交付", "闭合",
                 "全达成", "全满足", "已完成")
_WAIT_PAT = re.compile(r"待\s*(R\d+′)")


def _done_units() -> set[str]:
    units: set[str] = set()
    for ln in _read(DOC_19):
        if any(m in ln for m in _DONE_MARKERS):
            units.update(re.findall(r"R\d+′", ln))
    return units


def test_t_c_stale_wait_scanner():
    """已验收单号不得再以「待 Rxx′」形制出现（源头＝19 号）。"""
    done = _done_units()
    assert done, "19 号未提取到任何已验收单号——扫描器失效（源头缺失）"
    problems: list[str] = []
    for name in (*CURRENT_DOCS, USAGE_DOC):
        for i, ln in enumerate(_read(name), 1):
            for unit in _WAIT_PAT.findall(ln):
                if unit in done:
                    problems.append(
                        f"{DOC_DIR / name}:{i} 声明值=「待 {unit}」；"
                        f"实测值=19 号已判该单交付合格（行={ln.strip()[:90]}）")
    assert not problems, "\n".join(problems)


# ───────────────────────── T-D 时变量格式守卫 ─────────────────────────

def test_t_d_timevarying_format_guard():
    """CHANGELOG digest 清单**条目块**必须含「扫描时刻」＋日期；**不锁数值**。

    「条目块」＝以空行分隔的连续行块（`>` 引用的多行属同一块）——日期与「扫描时刻」
    可跨行出现（同一条目内即视为合格）。
    """
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    blocks: list[tuple[int, list[str]]] = []
    cur: list[str] = []
    start = 1
    for i, ln in enumerate(lines, 1):
        if ln.strip():
            if not cur:
                start = i
            cur.append(ln)
        elif cur:
            blocks.append((start, cur))
            cur = []
    if cur:
        blocks.append((start, cur))

    hit_blocks = [(i, b) for i, b in blocks if any("扫描时刻" in ln for ln in b)]
    assert hit_blocks, (f"{CHANGELOG}:— 声明值=缺「扫描时刻」条目块；"
                        f"实测值=digest 链计数系时变量、必须带扫描时刻（R60′/T-D）")
    bad = []
    for i, b in hit_blocks:
        text = "\n".join(b)
        if not re.search(r"\d{4}-\d{2}-\d{2}", text):
            bad.append((i, b[0]))
    assert not bad, "\n".join(
        f"{CHANGELOG}:{i} 声明值=条目块有「扫描时刻」但无日期；"
        f"实测值=须含 YYYY-MM-DD（块首行={ln.strip()[:90]}）" for i, ln in bad)
