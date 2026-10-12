# -*- coding: utf-8 -*-
"""**R37′（N43）＋R42′（N45）**：v77 三档 `metrics_digest` 基线重冻结校验。

背景（R37′）：§96.4 硬要求「v77 三档 digest 逐位不变」在 R26′（win_rate 改
**在场日口径**，DOC-11）后**唯一差异项＝win_rate**（22 个空仓日移出分母）⇒
属授权口径变更。本工具按**新口径**（`tools/v77_digest_baseline.json`）逐档校验：

  ① `metrics_digest` 与冻结值**逐位相同**（字符串等值）；
  ② EE-1 六项锚（excess / IR / n_fills / n_days / total_return）容差 1e-4。

默认入口（R42′/N45）：**不带 `--runs-dir` 也不落盘**——指标经**合法入口**
`btf.analytics.run_metrics.compute_run_metrics`（与落盘路径同一函数）计算；
`--runs-dir` 给出时才走 `store=`（落盘、`run_id`/`metrics` 由 RunStore 提供）。
两路 digest **必须一致**（同一计算入口）。

**退出码纪律（N45 补记 #12）**：判成败取**独立退出码**（0＝三档全过；1＝不匹配），
**不得经管道**（管道会吞退出码，历史上造成「崩溃被判通过」）。

用法：
    python tools/check_v77_digest.py [--tiers preRC,delivered,stress]
                                     [--runs-dir DIR] [--out JSON]
    python tools/check_v77_digest.py --smoke     # CI 冒烟：默认入口＋短窗单档
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

BTF = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BTF))
sys.path.insert(0, str(BTF / "tools"))

from btf.experiment.manifest import metrics_digest_of
from btf.runtime import BTFRuntime, make_store

import run_v77_robustness as rob

BASELINE = pathlib.Path(__file__).resolve().parent / "v77_digest_baseline.json"
TOL = 1e-4
_ANCHORS = ("excess", "ir", "total_return", "n_fills", "n_days")
_TIERS = ("preRC", "delivered", "stress")
#: `--smoke` 短窗（CI 冒烟专用；**不比对基线**——只验默认入口可运行、digest 可算）
_SMOKE_WINDOW = ("2024-01-02", "2024-06-28")


def _tier_cfg(profile: str, start: str | None = None,
              end: str | None = None) -> dict:
    s = start or rob.EE1["start"]
    e = end or rob.EE1["end"]
    cfg = rob._config(s, e, rob.EE1["top_k"], cost="delivered")
    if profile == "preRC":
        cfg["execution"] = {**cfg["execution"], **rob.COST_PROFILES["preRC"]}
    elif profile == "delivered":
        cfg["execution"] = {**cfg["execution"],
                            **rob.COST_PROFILES["delivered"]}
    else:
        cfg["execution"] = {
            **cfg["execution"],
            "slippage": {"model": "pct", "params": {"pct": 0.003}},
            "handler_params": {"volume_participation": 0.05},
        }
    return cfg


def _metrics_legal(rt, res) -> dict:
    """**R42′（N45）**：不落盘路径的**合法指标入口**（与落盘路径同一函数）。"""
    from btf import registry
    from btf.analytics.run_metrics import compute_run_metrics
    from btf.analytics.trades import normalize_consolidations  # R61′

    return compute_run_metrics(
        res.snapshots, res.fills, rt._analysis_config(),
        analyzer_names=rt._analyzer_names(),
        resolver=lambda name: registry.resolve(registry.ANALYZER, name),
        extras=rt._benchmark_metrics,
        # R61′（N63）：逐笔口径与落盘路径同源（v77 为纯股票、无折算 ⇒ 空序列）
        consolidations=normalize_consolidations(
            getattr(res, "consolidations", ())))


def _harden_stdout() -> None:
    """让 stdout 对不可编码字符降级而非抛错（2026-10-11 实证）。

    本工具用 `✓` / `✗` 报结论。stdout 被**管道/CI 捕获**时 Python 用 locale
    编码（GBK）⇒ `✓`(U+2713) 不在 GBK 中 ⇒ `print` 抛 UnicodeEncodeError，
    **check 明明通过却以退出码 1 结束**（`test_v77_digest_tool_smoke` 实测复现）。
    errors="replace" 把不可编码符号降级为 `?`：判定链（数字行）完全保真，
    只有符号本身降级 —— 与 `tools/doctor.py` 同源纪律。
    """
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass


def main() -> int:
    _harden_stdout()
    ap = argparse.ArgumentParser(description="v77 三档 digest 基线校验（R37′/R42′）")
    ap.add_argument("--tiers", default=",".join(_TIERS),
                    help="档位（逗号分隔；缺省三档全跑）")
    ap.add_argument("--runs-dir", default=None,
                    help="落盘目录（可选；缺省不落盘，走合法指标入口）")
    ap.add_argument("--out", default=None, help="实测 JSON 输出路径（可选）")
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--smoke", action="store_true",
                    help="CI 冒烟：默认入口＋短窗单档（preRC），不比对基线")
    args = ap.parse_args()

    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    if args.smoke:
        return _smoke()
    bad = [t for t in tiers if t not in _TIERS]
    if bad:
        raise SystemExit(f"未知档位：{bad}（可选 {_TIERS}）")

    baseline = json.loads(pathlib.Path(args.baseline).read_text(
        encoding="utf-8"))
    refs = baseline["baselines"]
    bench = rob._bench_series("000300.SH", rob.EE1["start"], rob.EE1["end"])
    runs_dir = pathlib.Path(args.runs_dir) if args.runs_dir else None
    print(f"EE-1 {rob.EE1['start']}~{rob.EE1['end']} | top_k="
          f"{rob.EE1['top_k']} | 档位 {tiers}", flush=True)
    print(f"模式：{'落盘（--runs-dir）' if args.runs_dir else '默认（不落盘）'}"
          f" | 基线 {args.baseline}", flush=True)

    rows: list[dict] = []
    all_ok = True
    for profile in tiers:
        cfg = _tier_cfg(profile)
        t0 = time.perf_counter()
        rt = BTFRuntime().load_config(cfg).build()
        if runs_dir is not None:
            store = make_store(str(runs_dir / profile))
            res = rt.run(store=store)
            metrics = dict(res.metrics)
            run_id = res.run_id
        else:
            res = rt.run(persist=False)      # **N45 曾在此崩溃**（无 .metrics）
            metrics = _metrics_legal(rt, res)
            run_id = f"(未落盘) {res.snapshots[0].date.to_ymd()}~" \
                     f"{res.snapshots[-1].date.to_ymd()}"
        wall = time.perf_counter() - t0
        nav = [(s.date.to_ymd(), s.total_value) for s in res.snapshots]
        ex = rob._excess_ir(nav, bench)
        actual = {
            "metrics_digest": metrics_digest_of(metrics),
            "excess": ex["excess_return"], "ir": ex["information_ratio"],
            "total_return": nav[-1][1] / nav[0][1] - 1.0,
            "n_fills": len(res.fills), "n_days": len(nav),
        }
        ref = refs[profile]
        digest_ok = actual["metrics_digest"] == ref["metrics_digest"]
        anchors_ok = {k: (actual[k] is not None
                          and abs(float(actual[k]) - float(ref[k])) <= TOL)
                      for k in _ANCHORS}
        # R38′ 旁证：纯股票（v77）run 的装配披露**不应**含 ETF 覆盖段
        has_coverage = any("公司行动覆盖段" in n for n in rt.assembly_notes)
        row = {"profile": profile, "run_id": run_id,
               "seconds": round(wall, 1), "digest": actual["metrics_digest"],
               "digest_ok": digest_ok, "anchors_ok": anchors_ok,
               "anchors_all_ok": all(anchors_ok.values()),
               "has_etf_coverage_note": has_coverage, **actual}
        rows.append(row)
        all_ok = all_ok and digest_ok and row["anchors_all_ok"]
        print(f"\n[{profile}] {wall:.1f}s run={run_id}", flush=True)
        print(f"    digest = {actual['metrics_digest']}", flush=True)
        print(f"    基线   = {ref['metrics_digest']}", flush=True)
        print(f"    ⇒ digest 逐位相同 = {digest_ok} | 锚 "
              f"{'全过' if row['anchors_all_ok'] else anchors_ok}", flush=True)
        print(f"    纯股票覆盖段守卫（R38′）：has_etf_coverage_note = "
              f"{has_coverage}（期望 False）", flush=True)

    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps({"frozen_at": baseline["frozen_at"], "rows": rows},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n实测 → {args.out}")
    if runs_dir is None:
        print("（默认模式：未落盘——指标走 compute_run_metrics 合法入口）")
    print("\n三档 digest＋锚：", "全部通过 ✓" if all_ok else "存在不匹配 ✗")
    return 0 if all_ok else 1


def _smoke() -> int:
    """**R42′（N45）**：默认入口冒烟——短窗单档（preRC），只验可运行＋digest 可算。"""
    start, end = _SMOKE_WINDOW
    cfg = _tier_cfg("preRC", start, end)
    t0 = time.perf_counter()
    rt = BTFRuntime().load_config(cfg).build()
    res = rt.run(persist=False)
    metrics = _metrics_legal(rt, res)
    dig = metrics_digest_of(metrics)
    wall = time.perf_counter() - t0
    print(f"[smoke] 默认入口（不落盘）{start}~{end} {wall:.1f}s | "
          f"n_days={len(res.snapshots)} n_fills={len(res.fills)}", flush=True)
    print(f"[smoke] digest = {dig}", flush=True)
    ok = dig.startswith("sha256:") and len(res.snapshots) > 0
    print("smoke OK ✓" if ok else "smoke FAILED ✗", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
