# -*- coding: utf-8 -*-
"""红利策略本地复现运行器（卡夏 REPORT.md §6.1 / §5.1 / §6.0b 对照）。

**锚定纪律**：真入口 `BTFRuntime` 跑，绩效由 btf 记账；本脚本只做
① 配置装配 ② 结果切片 ③ 落盘。**不自算收益/回撤/夏普**——分段指标由
btf 的 nav 序列逐月切片后按同一口径重算（窗口变化，不是另一套算法）。

用法：
    python tools/run_ext_dv.py --panel <panel.parquet> [--arms DV5,G5] [--dry-run]
退出码：0=全部成功 / 1=有失败臂
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "tools"))
from btf.config import paths

import run_v77_robustness as rob

MIRROR = Path(r"D:\量化策略\赤潮\rules_mirror_v77.json")
STRATEGY = "cc_strategies.ext_dv:ExtDvStrategy"

#: 臂：(id, variant, top_k, rank_key, 说明)
ARMS: list[tuple[str, str, int, str, str]] = [
    ("DV5", "DV", 5, "dv_ttm", "池内股息率前5（不排除/不质量）＝报告 DV5 17.21%"),
    ("A5", "A", 5, "dv_ttm", "负向排除+股息率前5＝报告 EXCL_DV5 18.69%"),
    ("G5", "G", 5, "dv_ttm", "全约束（主口径）＝报告 G 16.5%"),
    ("G10", "G", 10, "dv_ttm", "全约束 k=10＝报告 13.6%"),
    ("G5r", "G", 5, "dv_ratio", "全约束换 dv_ratio 排序＝报告 13.0%"),
]

START, END = "2016-01-04", "2026-09-30"
BENCH = "000922.CSI"          # 中证红利（价格指数，报告同口径）
BENCH2 = "000300.SH"


def _config(variant: str, top_k: int, rank_key: str, panel: str) -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": STRATEGY,
            "strategy_paths": ["D:/量化策略/赤潮/研究"],
            "params": {"variant": variant, "top_k": top_k,
                       "rank_key": rank_key, "panel_path": panel},
            "universe": {"source": "all"},
            "period": {"start": START, "end": END},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": "tushare_parquet"},
        "execution": {"handler": "next_open", "cost_model": "tiered_v1",
                      "rebalancer": "full",
                      "slippage": {"model": "pct", "params": {"pct": 0.001}}},
        "risk": {"rules": [{"name": "tradability"},
                           {"name": "max_weight", "params": {"max_weight": 0.95}},
                           {"name": "cash_check"}]},
        "rules": {"source": "mirror_json", "path": str(MIRROR)},
        "report": {"benchmark": "000300.SH"},
    }


def _slice_metrics(nav: list[tuple[str, float]], start: str, end: str) -> dict:
    """在 btf 的 nav 上切窗口 → 同口径重算（年化按自然日、MDD 按日收盘）。"""
    import datetime as dt

    s = [(d.replace("-", ""), v) for d, v in nav if start.replace("-", "") <= d.replace("-", "") <= end.replace("-", "")]
    if len(s) < 5:
        return {}
    v0, v1 = s[0][1], s[-1][1]
    yrs = (dt.date(int(s[-1][0][:4]), int(s[-1][0][4:6]), int(s[-1][0][6:8]))
           - dt.date(int(s[0][0][:4]), int(s[0][0][4:6]), int(s[0][0][6:8]))).days / 365.25
    ann = (v1 / v0) ** (1 / yrs) - 1 if yrs > 0 and v0 > 0 else float("nan")
    peak, mdd = s[0][1], 0.0
    for _d, v in s:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    rets = [s[i][1] / s[i - 1][1] - 1 for i in range(1, len(s))]
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / max(len(rets) - 1, 1)
    vol = var ** 0.5 * (252 ** 0.5)
    return {"年化": round(ann * 100, 2), "回撤": round(mdd * 100, 2),
            "波动": round(vol * 100, 2),
            "夏普": round(ann / vol, 2) if vol > 0 else None,
            "区间": f"{s[0][0]}~{s[-1][0]}", "年数": round(yrs, 2)}


def run_arm(arm: tuple[str, str, int, str, str], panel: str, out_dir: Path) -> dict:
    aid, variant, top_k, rank_key, note = arm
    from btf.runtime import BTFRuntime, make_store

    t0 = time.perf_counter()
    rt = (BTFRuntime()
          .load_config(_config(variant, top_k, rank_key, panel))
          .build())
    result = rt.run(store=make_store(out_dir / "runs"))
    nav = [(s.date.to_ymd(), s.total_value) for s in result.snapshots]
    row: dict = {"arm": aid, "variant": variant, "top_k": top_k,
                 "rank_key": rank_key, "note": note,
                 "wall_s": round(time.perf_counter() - t0, 1),
                 "n_days": result.n_days, "run_id": result.run_id}
    for k in ("total_return", "annualized_return", "sharpe_ratio",
              "max_drawdown", "calmar_ratio", "total_turnover", "fee_ratio",
              "n_fills"):
        row[k] = result.metrics.get(k)
    row["全区间"] = _slice_metrics(nav, START, END)
    row["段1_2016_2020"] = _slice_metrics(nav, "2016-01-04", "2020-12-31")
    row["段2_2021_2026"] = _slice_metrics(nav, "2021-01-01", END)
    for bench in (BENCH, BENCH2):
        series = rob._bench_series(bench, START, END)
        row[bench] = rob._excess_ir(nav, series)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--arms", default="")
    ap.add_argument("--out", default=str(paths.OUTPUT_DIR / "ext-dv"))
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    want = {a.strip() for a in args.arms.split(",") if a.strip()}
    arms = [a for a in ARMS if not want or a[0] in want]

    rows, fails = [], []
    for arm in arms:
        print(f"[ext-dv] {arm[0]} ...", flush=True)
        try:
            rows.append(run_arm(arm, args.panel, out_dir))
            r = rows[-1]
            print(f"        年化 {r['annualized_return'] * 100:.2f}% "
                  f"回撤 {r['max_drawdown'] * 100:.2f}% 夏普 {r['sharpe_ratio']} "
                  f"换手 {r['total_turnover']:.2f} 成交 {r['n_fills']} "
                  f"({r['wall_s']:.0f}s)", flush=True)
        except Exception as exc:                     # 不吞：如实登记
            fails.append((arm[0], repr(exc)))
            print(f"        FAIL {exc!r}", flush=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"ext-dv-{stamp}.json"
    path.write_text(json.dumps({"start": START, "end": END, "panel": args.panel,
                                "bench": BENCH, "rows": rows,
                                "fails": fails}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"\n[ext-dv] 落盘 {path}")
    if fails:
        print(f"[ext-dv] 失败 {len(fails)} 臂：{fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
