# -*- coding: utf-8 -*-
"""schema_guard.py — 「代码/文档会用到的配置键」vs「schema 声明」一致性守卫
（本地工具，只读 btf 源码与 schema，不改任何 btf 核心文件）

为什么需要（2026-10-21 两次事故，同源）
    事故① `bt run --delisted-settlement writeoff` 会往 `run` 段注入 `delisted_settlement`，
           而本副本 schema 来自 btf 1.1.0、未声明该键 ⇒ 配置校验直接拒绝。
    事故② 为修①而整体换回 1.1.0 schema，**连带删掉了 v1.2.4 的 `run.price_view`** ⇒
           另一批配置（含已完成研究的复现配置）全部挂在 config-check。
    共性：**代码/用户会写的键 与 schema 声明的键 不同步**，而平台没有守卫。

三类检查（三原则：全列、不静默、含负向测试）
    A. CLI 硬编码注入 —— `btf/cli/main.py` 中 `overrides.setdefault("sec", {})["key"]`
    B. 内置默认      —— `btf/config/loader.py::DEFAULTS` 的全部点号路径
    C. 运行期消费键  —— `btf/runtime|engine` 通过 `run_cfg/exec_cfg/risk_cfg/data_cfg.get("k")`
                       读取的键（下表静态登记；新增消费点须同步此表）

用法
    python schema_guard.py                       # 默认 schema
    python schema_guard.py --schema <other.json>  # 回归对照（如历史备份）
    python schema_guard.py --no-selftest --json
退出码：0 = 全部已在 schema 声明；1 = 有缺失或自检失败；2 = 环境/参数错误
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
CLI = os.path.join(PROJ, "btf", "cli", "main.py")
LOADER = os.path.join(PROJ, "btf", "config", "loader.py")
SCHEMA = os.path.join(PROJ, "schemas", "backtest.v1.json")

#: C 类：运行时真正消费的配置键（来自 2026-10-21 grep `btf/runtime|engine|config`）
#: 新增 run_cfg/exec_cfg/... .get("x") 消费点时必须同步登记，否则守卫不覆盖。
RUNTIME_KEYS: dict[str, set[str]] = {
    "run": {"strategy", "strategy_paths", "params", "period", "universe",
            "initial_cash", "price_view", "delisted_settlement",
            "event_log"},
    "execution": {"handler", "handler_params", "cost_model", "cost_model_params",
                  "slippage", "rebalancer", "rebalancer_params"},
    "risk": {"rules", "allow_empty_chain"},
    "data": {"feed", "feed_params", "dataset", "quality", "fingerprint_mode",
             "allow_degraded_limit"},
    "report": {"benchmark", "sections"},
    "analysis": {"annualization_factor", "risk_free_rate", "metrics"},
    "rules": {"source", "path", "overrides"},
    "__top__": {"schema_version"},
}


#: 已知缺口：runtime 会读、但 schema 未声明（⇒ 用户无法通过 YAML 设置）。
#: 报告里**每次显式列出**（不静默），但不判失败；修好后应从本表移除。
KNOWN_GAPS: dict[str, str] = {}


# ---------------------------------------------------------------- 读取
def load_schema(path: str = SCHEMA) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def cli_injections(path: str = CLI) -> set[str]:
    src = open(path, encoding="utf-8").read()
    pat = re.compile(r'overrides\.setdefault\(\s*"([A-Za-z0-9_]+)"\s*,\s*\{\}\s*\)'
                     r'\s*\[\s*"([A-Za-z0-9_]+)"\s*\]')
    return {f"{a}.{b}" for a, b in pat.findall(src)}


def loader_defaults(path: str = LOADER) -> set[str]:
    """ast 解析 DEFAULTS 字面量 → 全部点号路径（含中间节点）。"""
    import ast

    src = open(path, encoding="utf-8").read()
    m = re.search(r"DEFAULTS:\s*dict\[str,\s*Any\]\s*=\s*(\{)", src)
    if not m:
        return set()
    i = m.start(1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
    obj = ast.literal_eval(src[i:j + 1])
    out: set[str] = set()

    def walk(o, prefix=""):
        if isinstance(o, dict):
            for k, v in o.items():
                p = f"{prefix}.{k}" if prefix else str(k)
                out.add(p)
                walk(v, p)

    walk(obj)
    return out


def runtime_keys() -> set[str]:
    return {f"{sec}.{k}" if sec != "__top__" else k
            for sec, keys in RUNTIME_KEYS.items() for k in keys}


# ---------------------------------------------------------------- schema 路径
def schema_paths(node: dict) -> tuple[set[str], set[str]]:
    """→ (已声明点号路径集, "开放对象"节点集)。

    开放判定：`type == "object"` 且未显式 `additionalProperties: false`
    （JSON Schema 默认允许额外属性；`handler_params`/`slippage.params` 即属此类）。
    """
    paths: set[str] = set()
    open_nodes: set[str] = set()

    def walk(n, pre):
        if not isinstance(n, dict):
            return
        if n.get("type") == "object" and n.get("additionalProperties") is not False:
            open_nodes.add(pre)
        for k, sub in (n.get("properties") or {}).items():
            p = f"{pre}.{k}" if pre else k
            paths.add(p)
            walk(sub, p)
        for key in ("items", "allOf", "anyOf", "oneOf"):
            sub = n.get(key)
            if isinstance(sub, dict):
                walk(sub, pre)
            elif isinstance(sub, list):
                for x in sub:
                    walk(x, pre)

    walk(node, "")
    return paths, open_nodes


def is_allowed(path: str, paths: set[str], open_nodes: set[str]) -> bool:
    if path in paths:
        return True
    parts = path.split(".")
    for i in range(len(parts) - 1, 0, -1):
        if ".".join(parts[:i]) in open_nodes:
            return True
    if "" in open_nodes:          # 根开放
        return True
    return False


# ---------------------------------------------------------------- 检查
def check(paths: set[str], open_nodes: set[str], groups: dict[str, set[str]],
          known: dict[str, str] | None = None) -> dict:
    known = KNOWN_GAPS if known is None else known
    res = {}
    for name, keys in groups.items():
        miss = sorted(k for k in keys if not is_allowed(k, paths, open_nodes))
        res[name] = {"checked": len(keys),
                     "missing": miss,
                     "known_gaps": [k for k in miss if k in known],
                     "fatal": [k for k in miss if k not in known]}
    res["ok"] = all(not g["fatal"] for k, g in res.items() if isinstance(g, dict))
    return res


def report(r: dict) -> str:
    L = []
    total = sum(g["checked"] for k, g in r.items() if isinstance(g, dict))
    L.append(f"检查项合计 {total} 个键（A CLI 注入 / B 内置默认 / C 运行期消费）")
    for name, label in (("A", "CLI 硬编码注入"), ("B", "内置默认"), ("C", "运行期消费键")):
        g = r[name]
        if g["fatal"]:
            L.append(f"❌ {label}（{g['checked']}）：schema 未声明 {len(g['fatal'])} 个")
            L += [f"     - {x}" for x in g["fatal"]]
        elif g["known_gaps"]:
            L.append(f"⚠️ {label}（{g['checked']}）：全部可声明之外，另有 {len(g['known_gaps'])} 个已知缺口")
        else:
            L.append(f"✅ {label}（{g['checked']}）：全部已声明")
    gaps = [k for g in r.values() if isinstance(g, dict) for k in g.get("known_gaps", [])]
    if gaps:
        L.append("  已知缺口明细（不判失败，须逐条修）：")
        L += [f"     · {k} —— {KNOWN_GAPS.get(k, '')}" for k in gaps]
    return chr(10).join(L)


def selftest() -> bool:
    """负向自检：故意喂不存在的键，必须被抓；开放节点必须放行。"""
    paths = {"run.strategy", "schema_version"}
    base = {"A": {"run.strategy"}, "B": {"schema_version"}, "C": {"run.strategy"}}
    ok_case = check(paths, set(), base)
    bad = {"A": {"run.__fake_a__"}, "B": {"__fake_b__"}, "C": {"run.__fake_c__"}}
    bad_case = check(paths, set(), bad, known={})
    # 已知缺口不应被判失败（但必须出现在 missing 里）
    gap_case = check(paths, set(), {"A": {"run.known_gap_x"}, "B": set(), "C": set()},
                     known={"run.known_gap_x": "test"})
    open_case = check(paths, {"data"}, {"A": {"data.any_new_key"}, "B": set(), "C": set()})
    p1 = ok_case["ok"] is True
    p4 = (gap_case["ok"] is True and "run.known_gap_x" in gap_case["A"]["known_gaps"])
    p2 = (bad_case["ok"] is False
          and "run.__fake_a__" in bad_case["A"]["missing"]
          and "__fake_b__" in bad_case["B"]["missing"]
          and "run.__fake_c__" in bad_case["C"]["missing"])
    p3 = open_case["ok"] is True
    print(f"[selftest] 正例通过={p1} / 三类负例全被抓={p2} / 开放节点放行={p3} / "
          f"已知缺口不判失败={p4} → " + ("PASS" if all((p1, p2, p3, p4)) else "FAIL"))
    return all((p1, p2, p3, p4))


def main() -> int:
    ap = argparse.ArgumentParser(description="配置键 vs schema 一致性守卫")
    ap.add_argument("--schema", default=SCHEMA)
    ap.add_argument("--no-selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    for p in (CLI, LOADER, a.schema):
        if not os.path.exists(p):
            print(f"[错误] 文件不存在：{p}", file=sys.stderr)
            return 2
    paths, open_nodes = schema_paths(load_schema(a.schema))
    groups = {"A": cli_injections(), "B": loader_defaults(), "C": runtime_keys()}
    r = check(paths, open_nodes, groups)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(f"[schema] {a.schema}")
        print(report(r))
    st = True if a.no_selftest else selftest()
    ok = r["ok"] and st
    print(f"[result] exit={'0（一致）' if ok else '1（存在缺失/自检失败）'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
