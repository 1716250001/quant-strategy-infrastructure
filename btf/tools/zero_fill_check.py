# -*- coding: utf-8 -*-
"""zero_fill_check.py —— 零成交 run 体检（只读）

为什么需要
----------
回测「跑通」不等于「跑出了信息」。本轮实测（2026-10-22）发现：一个 3 个月区间的 run
可以**正常完成、exit=0、verify digest 一致**，但 `n_fills = 0`、`total_return = 0`、
`sharpe = null`——指标全 0 却没有任何告警。若这类产物被当作「策略在该区间表现平淡」
引用，就是**用空跑冒充结论**。

判据
----
    n_fills == 0  ⇒ EMPTY（零成交；不得作为策略表现证据）
    n_fills > 0 且 |total_return| < 1e-12 ⇒ SUSPECT（有成交但净值不动，多为未建仓/即时平仓）
    其余 ⇒ OK

用法
----
    python -X utf8 tools/zero_fill_check.py --root <WS>/.context
    python -X utf8 tools/zero_fill_check.py --root <WS>/.context --csv out.csv
    python -X utf8 tools/zero_fill_check.py --selftest

退出码：0 = 无 EMPTY/SUSPECT｜1 = 有命中｜2 = 环境错误
（对齐 AGENTS「校验三原则」：exit 语义化、内置负向测试、未命中项显式列出）
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_SKIP = {".venv-btf", "__pycache__", ".git", ".ruff_cache", "node_modules"}


def _iter_runs(root: Path):
    for p in root.rglob("metrics.json"):
        if any(part in _SKIP for part in p.parts):
            continue
        yield p


def _unwrap(d: dict) -> dict:
    """metrics.json 是 {"contract_version":..., "metrics":{...}} 的嵌套结构。

    引擎产物的真实指标在 ``metrics.metrics`` 里；早期/手写产物可能直接平铺。
    两层都支持，否则会把全部 run 误判为 UNKNOWN（本轮实测踩过）。
    """
    inner = d.get("metrics")
    return inner if isinstance(inner, dict) else d


def _num(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                continue
    return default


def classify(metrics: dict) -> tuple[str, str]:
    """→ (档位, 说明)。EMPTY / SUSPECT / OK / UNKNOWN"""
    m = _unwrap(metrics)
    nf = _num(m, "n_fills", "n_trades", "fills")
    tr = _num(m, "total_return", "total_return_pct")
    if nf is None:
        return "UNKNOWN", "metrics 无 n_fills 字段"
    if nf == 0:
        return "EMPTY", "n_fills=0（零成交，指标全 0 不构成表现证据）"
    if tr is not None and abs(tr) < 1e-12:
        return "SUSPECT", f"n_fills={nf:.0f} 但 total_return≈0"
    return "OK", f"n_fills={nf:.0f}"


def selftest() -> bool:
    cases = [
        ({"n_fills": 0, "total_return": 0.0}, "EMPTY"),
        ({"n_fills": 5, "total_return": 0.0}, "SUSPECT"),
        ({"n_fills": 5, "total_return": 0.12}, "OK"),
        ({"total_return": 0.0}, "UNKNOWN"),
        # 嵌套结构（引擎真实形态）必须同样识别
        ({"contract_version": "runresult.v1",
          "metrics": {"n_fills": 0.0, "total_return": 0.0}}, "EMPTY"),
    ]
    ok = True
    for m, want in cases:
        got, _ = classify(m)
        print(f"[selftest] {m} → {got}（期望 {want}） {'✓' if got == want else '✗'}")
        ok &= got == want
    print(f"[selftest] {'PASS' if ok else 'FAIL'}")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="零成交 run 体检（只读）")
    ap.add_argument("--root", help="递归扫描根目录")
    ap.add_argument("--csv", help="把命中明细写到 CSV")
    ap.add_argument("--selftest", action="store_true", help="先跑负向/边界测试")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印全部 run")
    a = ap.parse_args(argv)

    try:
        if a.selftest and not selftest():
            print("[result] exit=1（selftest 失败）")
            return 1
        if not a.root:
            print("[env] 需要 --root")
            return 2
        root = Path(a.root)
        if not root.exists():
            print(f"[env] 找不到 {root}")
            return 2

        rows = []
        cnt = Counter()
        for m in sorted(_iter_runs(root)):
            try:
                d = json.loads(m.read_text(encoding="utf-8"))
            except Exception as exc:
                cnt["UNREADABLE"] += 1
                rows.append((str(m.parent), "UNREADABLE", str(exc)))
                continue
            if not isinstance(d, dict):
                cnt["UNREADABLE"] += 1
                rows.append((str(m.parent), "UNREADABLE", "metrics 非 dict"))
                continue
            verdict, why = classify(d)
            cnt[verdict] += 1
            rows.append((str(m.parent), verdict, why))
            if a.verbose or verdict in ("EMPTY", "SUSPECT"):
                print(f"[{verdict:9s}] {m.parent.name:32s} {why}")

        total = sum(cnt.values())
        print()
        print("=" * 78)
        print(f"扫描 run 合计 {total}")
        for k in ("OK", "EMPTY", "SUSPECT", "UNKNOWN", "UNREADABLE"):
            print(f"  {k:10s} {cnt.get(k, 0)}")
        print("-" * 78)
        hits = [r for r in rows if r[1] in ("EMPTY", "SUSPECT")]
        print(f"【命中】{len(hits)} 个（EMPTY+SUSPECT）")
        if not hits:
            print("  (无)")
        else:
            for p, v, why in hits[:100]:
                print(f"  - [{v}] {p} → {why}")
            if len(hits) > 100:
                print(f"  ... 另有 {len(hits) - 100} 个未显示（总数已计数）")
        unknown = [r for r in rows if r[1] == "UNKNOWN"]
        print(f"【无法判定】{len(unknown)} 个")
        if not unknown:
            print("  (无)")
        else:
            for p, _v, why in unknown[:30]:
                print(f"  - {p} → {why}")

        if a.csv:
            import csv as _csv

            with open(a.csv, "w", newline="", encoding="utf-8-sig") as fh:
                w = _csv.writer(fh)
                w.writerow(["run_dir", "verdict", "reason"])
                w.writerows(rows)
            print(f"[csv] {a.csv}")

        code = 0 if (cnt.get("EMPTY", 0) == 0 and cnt.get("SUSPECT", 0) == 0) else 1
        print(f"[result] exit={code}（{'干净' if code == 0 else '有零成交/疑似空跑'}）")
        return code
    except Exception as exc:
        print(f"[env] {type(exc).__name__}: {exc}")
        print("[result] exit=2（环境错误）")
        return 2


if __name__ == "__main__":
    sys.exit(main())
