# -*- coding: utf-8 -*-
"""规则四方机检（M3 任务 6.1；H2 单一真源）。

    python tools/check_rules_four_way.py [--root d:/量化策略/赤潮]
                                         [--json .../rules_mirror_v77.json]

四方（任一不一致 → FAIL，退出码 2）：
    ① 规则源  赤潮/rules/single-source.md（v7.7，人读权威）
    ② 代码镜像 赤潮/md_core/paths.py（V75_DEFAULTS / LIQ_THRESHOLDS）
    ③ 消费绑定 赤潮/md_core/screening.py（screen_stocks_v75 默认参数）
    ④ btf 缓存 rules_mirror_v77.json（export_rules_mirror.py 导出）

外加**状态机语义锚点**（X-4；2026-09-28）：上述四方只比阈值**数值**，
X-1 的 RECOVERY 自我维持 bug 正是在 8 个阈值全对的情况下逃逸的——故补
`run_state_machine_checks`：源「3 日恢复」× md_core「连续 3 日 status」
× btf `_finalize` 窗口结构 + **自我维持回归守卫**。

设计（沿用赤潮 `scripts/check_rule_mirror.py` 风格）：
    - **纯正则解析源码文本**：零 import、零数据依赖——任意解释器可跑，
      也便于在本仓库内用合成 fixture 做单测；
    - 本脚本位于 btf 侧（**不修改**赤潮既有三方脚本，18 号第七节集成约定
      "不修改现有工程"），第四方 JSON 即 btf 规则缓存，纳入机检清单。

口径：L2_filter 键名用 md_core 口径（pe_max/pb_max/roe_min/dv_min，
roe/dv 为**百分数**）；文档侧键名（pe_ttm_max/roe_waa_min）经 JSON 的
`aliases` 映射，不参与本脚本比对。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EXIT_PASS, EXIT_FAIL, EXIT_ERROR = 0, 2, 3

#: L0-LIQ 规则源抓取（CRISIS / WATCH 两段；原文为单行自然语言）
_RE_CRISIS = re.compile(
    r"CRISIS=上证≤\s*(-?[\d.]+)%.*?跌停占比≥\s*([\d.]+)%.*?跌停≥\s*(\d+)"
    r".*?小盘≤\s*(-?[\d.]+)%", re.S)
_RE_WATCH = re.compile(
    r"WATCH=上证≤\s*(-?[\d.]+)%.*?跌停≥\s*(\d+).*?跌停占比≥\s*([\d.]+)%"
    r".*?小盘≤\s*(-?[\d.]+)%", re.S)
#: L2 硬筛子（锚点：六层体系代码块 "PE<30+PB<3+ROE>5%+股息率>1%"）
_RE_L2 = re.compile(
    r"PE<\s*([\d.]+)\s*\+\s*PB<\s*([\d.]+)\s*\+\s*ROE>\s*([\d.]+)%\s*\+"
    r"\s*股息率>\s*([\d.]+)%", re.S)

# ── 状态机语义锚点（X-4 ／ Z-6；19 号 §17.3.5 / §26.5）──
# 阈值数值一致 ≠ 状态机语义正确：X-1 的 RECOVERY 自我维持 bug 在 8 个阈值
# 全对的情况下污染了十年 27.1% 的交易日；Z-6 又把「入闸确认期归属」写进规则源。
# 故机检补**语义结构**锚点：
#   ① 规则源：RECOVERY「3 日恢复」机制 + **入闸确认期归属 = CRISIS**（Z-6）
#   ② md_core：RECOVERY 由调用方「连续 3 日 status」判定（声明存在）
#   ③ btf 实现：① 确认期窗口 `days + 1 < confirm_days`（默认 3，期间 status=CRISIS）
#              ② 恢复期窗口 `days + 1 < recovery_days`（默认 3）
#              ③ 机器记忆 `phase_days`（单步递推，不用长历史）
#   守卫三类回归：`RECOVERY in prev_*`（X-1 自我维持）／确认期窗口被移除
#   （Z-6 回退：危机次日即恢复）／窗口默认值 ≠ 规则源 3 日
_RE_SOURCE_3DAY = re.compile(r"RECOVERY[^\n|]*?3\s*日[^\n|]*?NORMAL|3\s*日恢复机制")
#: Z-6：规则源须写明「入闸确认期」及其状态归属（CRISIS）
_RE_SOURCE_CONFIRM = re.compile(r"确认期[^\n|]*?CRISIS")
_RE_MDCORE_3DAY = re.compile(r"连续\s*3\s*日\s*status|3\s*日恢复机制")
#: `_code_only` 以空格拼接 token，故用宽松空白匹配代码结构
_RE_BTF_PHASE = re.compile(r"phase_days")
_RE_BTF_CONFIRM = re.compile(r"days\s*\+\s*1\s*<\s*confirm_days")
_RE_BTF_WINDOW = re.compile(r"days\s*\+\s*1\s*<\s*recovery_days")
_RE_BTF_SELF_HOLD = re.compile(r"RECOVERY\s+in\s+prev_(?:statuses|raw)")
_RE_BTF_CONFIRM_DAYS = re.compile(r"confirm_days\s*:\s*int\s*=\s*(\d+)")
_RE_BTF_RECOVERY_DAYS = re.compile(r"recovery_days\s*:\s*int\s*=\s*(\d+)")
#: md_core/paths.py 字典块
_RE_V75 = re.compile(r"V75_DEFAULTS\s*=\s*\{(.*?)\}", re.S)
_RE_LIQ = re.compile(r"LIQ_THRESHOLDS\s*=\s*\{(.*?)\}", re.S)
_RE_KV = re.compile(r"[\"']([a-z_]+)[\"']\s*:\s*(-?[\d.]+)")
#: screening.py 默认参数绑定
_RE_BIND = re.compile(r"([a-z_]+)\s*=\s*paths\.V75_DEFAULTS\[[\"']([a-z_]+)[\"']\]")

_LIQ_KEYS = ("sh_crisis", "sh_watch", "small_crisis", "small_watch",
             "down_crisis", "down_watch", "ratio_crisis", "ratio_watch")


def _nums(text: str, pattern: re.Pattern[str], cast=float) -> list:
    match = pattern.search(text)
    if not match:
        return []
    return [cast(g) for g in match.groups()]


def grab_source(path: Path) -> dict[str, dict[str, float]]:
    """① 规则源 → {L0_LIQ: {...}, L2_filter: {...}}。"""
    text = path.read_text(encoding="utf-8")
    crisis, watch = _nums(text, _RE_CRISIS), _nums(text, _RE_WATCH)
    liq: dict[str, float] = {}
    if len(crisis) == 4 and len(watch) == 4:
        liq = {"sh_crisis": crisis[0], "ratio_crisis": crisis[1],
               "down_crisis": crisis[2], "small_crisis": crisis[3],
               "sh_watch": watch[0], "down_watch": watch[1],
               "ratio_watch": watch[2], "small_watch": watch[3]}
    l2_raw = _nums(text, _RE_L2)
    l2: dict[str, float] = {}
    if len(l2_raw) == 4:
        l2 = {"pe_max": l2_raw[0], "pb_max": l2_raw[1],
              "roe_min": l2_raw[2], "dv_min": l2_raw[3]}
    return {"L0_LIQ": liq, "L2_filter": l2}


def grab_paths(path: Path) -> dict[str, dict[str, float]]:
    """② md_core/paths.py 镜像常量。"""
    text = path.read_text(encoding="utf-8")
    v75_match, liq_match = _RE_V75.search(text), _RE_LIQ.search(text)
    def _as_float(block: str) -> dict[str, float]:
        return {k: float(v) for k, v in _RE_KV.findall(block)}

    return {
        "L2_filter": _as_float(v75_match.group(1)) if v75_match else {},
        "L0_LIQ": _as_float(liq_match.group(1)) if liq_match else {},
    }


def grab_screening(path: Path) -> dict[str, str]:
    """③ screening.py 默认参数绑定：参数名 → 镜像键名。"""
    text = path.read_text(encoding="utf-8")
    return {name: key for name, key in _RE_BIND.findall(text)}


def load_mirror_json(path: Path) -> dict[str, dict[str, float]]:
    """④ btf 规则缓存 JSON。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rules = payload.get("rules") or {}
    return {
        "L2_filter": {k: float(v) for k, v in (rules.get("L2_filter") or {}).items()},
        "L0_LIQ": {k: float(v) for k, v in (rules.get("L0_LIQ") or {}).items()},
    }


def _diff(a: dict, b: dict) -> list[str]:
    out = []
    for key in sorted(set(a) | set(b)):
        if key not in a:
            out.append(f"{key}: 前者缺失")
        elif key not in b:
            out.append(f"{key}: 后者缺失")
        elif abs(float(a[key]) - float(b[key])) > 1e-9:
            out.append(f"{key}: {a[key]} ≠ {b[key]}")
    return out


def _code_only(text: str) -> str:
    """去注释与字符串字面量（含 docstring）——语义锚点只扫**代码结构**。

    否则修复留痕里引用的旧代码文本（`docstring` 中「…`RECOVERY in
    prev_statuses[:1]`…」）会被正则误判为自我维持回归（2026-09-28 实测踩到）。
    tokenize 失败（非法片段）时退回原文扫描——宁可误报，不静默放行。
    """
    import io
    import tokenize

    try:
        kept = [tok.string for tok in tokenize.generate_tokens(
            io.StringIO(text).readline)
            if tok.type not in (tokenize.COMMENT, tokenize.STRING)]
    except (SyntaxError, tokenize.TokenError, ValueError):
        return text
    return " ".join(kept)


def run_state_machine_checks(root: Path, btf_root: Path) -> list[str]:
    """状态机语义四方锚点（X-4）：RECOVERY「3 日恢复」口径的结构机检。

    与 `run_checks` 的分工：后者比**阈值数值**，本函数比**状态机语义结构**——
    数值全对而语义错正是 X-1 逃逸的原因（§17.3.5）。
    """
    fails: list[str] = []
    source_path = root / "rules" / "single-source.md"
    mdcore_path = root / "md_core" / "market_state.py"
    liq_path = btf_root / "btf" / "data" / "liq.py"

    source_text = source_path.read_text(encoding="utf-8")
    if _RE_SOURCE_3DAY.search(source_text) is None:
        fails.append("① 规则源未找到 RECOVERY「3 日恢复」机制表述（锚点失效？）")
    if _RE_SOURCE_CONFIRM.search(source_text) is None:
        fails.append("① 规则源未写明「入闸确认期状态归属 = CRISIS」（Z-6 澄清缺失）")
    if _RE_MDCORE_3DAY.search(mdcore_path.read_text(encoding="utf-8")) is None:
        fails.append("② md_core/market_state.py 未声明「连续 3 日 status」判定")
    if not liq_path.is_file():
        fails.append(f"③ btf 实现缺失: {liq_path}")
        return fails

    text = _code_only(liq_path.read_text(encoding="utf-8"))
    if _RE_BTF_CONFIRM.search(text) is None:
        fails.append("③ btf `_finalize` 缺「入闸确认期」判定"
                     "（`days + 1 < confirm_days`）——Z-6 回退：危机次日即恢复")
    if _RE_BTF_WINDOW.search(text) is None:
        fails.append("③ btf `_finalize` 缺恢复期窗口判定"
                     "（`days + 1 < recovery_days`）")
    if _RE_BTF_PHASE.search(text) is None:
        fails.append("③ btf 缺状态机记忆 `phase_days`（单步递推）")
    held = _RE_BTF_SELF_HOLD.search(text)
    if held is not None:
        fails.append(
            f"③ btf `_finalize` 出现 RECOVERY 自我维持（{held.group(0)!r}）——"
            f"X-1 回归：状态一旦进入 RECOVERY 将永不退出")
    for name, pattern, label in (
            ("confirm_days", _RE_BTF_CONFIRM_DAYS, "入闸确认期"),
            ("recovery_days", _RE_BTF_RECOVERY_DAYS, "恢复期")):
        found = pattern.search(text)
        if found is None:
            fails.append(f"③ btf 未声明 {name} 默认值（{label}窗口）")
        elif int(found.group(1)) != 3:
            fails.append(f"③ btf {name} 默认 {found.group(1)} ≠ 规则源 3 日")
    return fails


def run_checks(root: Path, json_path: Path) -> list[str]:
    """四方比对 → 失败项清单（空 = PASS）。"""
    fails: list[str] = []
    source = grab_source(root / "rules" / "single-source.md")
    mirror = grab_paths(root / "md_core" / "paths.py")
    binds = grab_screening(root / "md_core" / "screening.py")
    cache = load_mirror_json(json_path)

    for group in ("L2_filter", "L0_LIQ"):
        if not source[group]:
            fails.append(f"① 规则源未抓到 {group}（正则锚点失效？）")
        if not mirror[group]:
            fails.append(f"② md_core/paths.py 未抓到 {group}")
        if not cache[group]:
            fails.append(f"④ {json_path.name} 缺 {group}")
        for label, left, right in (
            ("①源 vs ②镜像", source[group], mirror[group]),
            ("②镜像 vs ④btf缓存", mirror[group], cache[group]),
            ("①源 vs ④btf缓存", source[group], cache[group]),
        ):
            for item in _diff(left, right):
                fails.append(f"{label}｜{group}｜{item}")

    # ③ 消费绑定：四个 L2 参数必须绑定 paths.V75_DEFAULTS（防手抄）
    for key in ("pe_max", "pb_max", "roe_min", "dv_min"):
        if key not in binds.values():
            fails.append(f"③ screening.py 未绑定 {key}=paths.V75_DEFAULTS[...]")
    return fails


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="规则四方机检（H2 单一真源）")
    parser.add_argument("--root", default=r"d:\量化策略\赤潮", help="赤潮根目录")
    parser.add_argument("--json", default=r"D:\量化策略\赤潮\rules_mirror_v77.json",
                        help="btf 规则缓存 JSON")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root, json_path = Path(args.root), Path(args.json)
    for need in (root / "rules" / "single-source.md", root / "md_core" / "paths.py",
                 root / "md_core" / "screening.py", json_path):
        if not need.is_file():
            print(f"[error] 缺失: {need}", file=sys.stderr)
            return EXIT_ERROR

    btf_root = Path(__file__).resolve().parent.parent
    fails = (run_checks(root, json_path)
             + run_state_machine_checks(root, btf_root))
    if fails:
        print(f"[FAIL] 规则机检：{len(fails)} 处不一致（数值四方 + 状态机语义锚点）")
        for item in fails:
            print(f"   - {item}")
        return EXIT_FAIL
    print("[PASS] 规则机检：源 ↔ 镜像 ↔ 消费绑定 ↔ btf 缓存 数值一致；"
          "状态机 RECOVERY「3 日恢复」语义锚点一致")
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
