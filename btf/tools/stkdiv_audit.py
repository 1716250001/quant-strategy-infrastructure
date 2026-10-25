# -*- coding: utf-8 -*-
"""stkdiv_audit.py —— 送转股与复权因子不一致导致的「凭空增益」审计（只读，P14）

缺陷（2026-10-21 发现）
----------------------
`dividend` 表里存在 `div_proc=实施 ∧ stk_div>0 ∧ ex_date 非空` 的事件，但**同一除权日的
`adj_factor` 完全没有变化**（正常送转应按 1/(1+stk_div) 下降）。btf 引擎按 `stk_div` 单向
**加股数**、却不调整价格 ⇒ 除权日市值凭空增加，「买入持有」类臂受害最重（持有全市场、
从不卖出，每个事件都会命中）。

全市场实测（as_of 2026-09-30，镜像 `D:/全量数据/market_data_btf`）
    dividend 实施且送转>0：**12,552** 个 (code, ex_date) 事件 / 4,391 只
    其中 adj_factor 在除权日**完全未变**（MISMATCH）：**74** 个事件 / 73 只  ← 本工具的风险集
    （其余 10,090 个属"因子变了但比例含现金分红，不等于 1/(1+stk_div)"，不判异常）

用法
    python stkdiv_audit.py --run <run_dir>            # 单 run：事件明细 + 修正年化/MDD
    python stkdiv_audit.py --all-runs <root>          # 递归全部 run，输出命中汇总
    python stkdiv_audit.py --events                   # 只重建/打印全市场 MISMATCH 风险集
    python stkdiv_audit.py --run <dir> --json
退出码：0 = 无命中；1 = 有命中；2 = 环境错误
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from btf.config.paths import MARKET_DATA_DIR

MIRROR = str(MARKET_DATA_DIR)
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stkdiv_mismatch_20261021.csv")


def build_events(force: bool = False) -> pd.DataFrame:
    """重建全市场「送转>0 但 adj_factor 未变」风险集。"""
    import pyarrow.parquet as pq

    if os.path.exists(CACHE) and not force:
        return pd.read_csv(CACHE, dtype={"code": str, "ex_date": str})
    rows = [pq.read_table(f).to_pandas() for f in
            sorted(glob.glob(os.path.join(MIRROR, "dividend", "*.parquet")))]
    d = pd.concat(rows, ignore_index=True)
    ev = d[(d.div_proc == "实施") & (d.stk_div.fillna(0) > 0) & d.ex_date.notna()].copy()
    ev["ex_date"] = ev.ex_date.astype(str)
    ev = ev[["ts_code", "ex_date", "stk_div"]].rename(columns={"ts_code": "code"})
    ev = ev.drop_duplicates(["code", "ex_date"])
    codes = set(ev.code)

    af = [pq.read_table(f).to_pandas() for f in
          sorted(glob.glob(os.path.join(MIRROR, "adj_factor", "*.parquet")))]
    a = pd.concat(af, ignore_index=True)
    a["trade_date"] = a.trade_date.astype(str)
    a = a[a.ts_code.isin(codes)].sort_values(["ts_code", "trade_date"])
    g = {c: (x.trade_date.to_numpy(), x.adj_factor.to_numpy(float))
         for c, x in a.groupby("ts_code")}

    keep = []
    for r in ev.itertuples(index=False):
        dt, ft = g.get(r.code, (None, None))
        if dt is None:
            continue
        i = int(np.searchsorted(dt, r.ex_date))
        if i == 0 or i >= len(dt):
            continue
        if abs(ft[i] / ft[i - 1] - 1.0) < 1e-12:        # 因子完全未变 ⇒ MISMATCH
            keep.append((r.code, r.ex_date, float(r.stk_div)))
    out = pd.DataFrame(keep, columns=["code", "ex_date", "stk_div"])
    out.to_csv(CACHE, index=False, encoding="utf-8-sig")
    return out


def find_runs(root: str) -> list[str]:
    out = []
    for dp, dns, fns in os.walk(root):
        if "metrics.json" in fns and "snapshots.jsonl" in fns:
            out.append(dp)
            dns[:] = []
    return sorted(out)


def load_snaps(rd: str) -> list[dict]:
    with open(os.path.join(rd, "snapshots.jsonl"), encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def run_hits(rd: str, risk: dict[tuple[str, str], float]) -> list[dict]:
    """从 events.jsonl 找本 run 命中的 (code, ex_date)（引擎自己记录了 corporate_action）。"""
    ev = os.path.join(rd, "events.jsonl")
    if not os.path.exists(ev):
        return []
    out = []
    with open(ev, encoding="utf-8") as fh:
        for line in fh:
            if "corporate_action" not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            act = (o.get("payload") or {}).get("action") or {}
            k = (act.get("symbol"), str(act.get("ex_date") or ""))
            if k in risk:
                out.append({"code": k[0], "ex_date": k[1], "stk_div": risk[k]})
    uniq = {(h["code"], h["ex_date"]): h for h in out}
    return list(uniq.values())


def quantify(rd: str, hits: list[dict]) -> dict:
    """逐事件算「凭空增益」并给出修正后的年化/波动/MDD。"""
    snaps = load_snaps(rd)
    if not snaps or not hits:
        return {"events": [], "has_hit": False}
    dates = [str(s.get("date", "")).replace("-", "") for s in snaps]
    nav = np.array([float(s.get("total_value") or 0.0) for s in snaps], float)
    recs, cut = [], np.zeros(len(nav))
    for h in hits:
        i = next((i for i, d in enumerate(dates) if d >= h["ex_date"]), None)
        if i is None or i == 0:
            continue
        qa = float((snaps[i - 1].get("positions_qty") or {}).get(h["code"]) or 0.0)
        qb = float((snaps[i].get("positions_qty") or {}).get(h["code"]) or 0.0)
        dq = qb - qa
        if dq <= 0:                                  # 未加股（已卖出/无持仓）⇒ 不构成凭空增益
            continue
        w = float((snaps[i].get("weights") or {}).get(h["code"]) or 0.0)
        px = (w * float(snaps[i].get("total_value") or 0.0) / qb) if qb else 0.0
        gain = dq * px
        # ⚠️ 路径修正必须**逐日**扣（三个坑）：
        #    ① 只用除权日那一个常数扣减，会在净值接近 0 的臂上把终值压成负数（假 −100%）；
        #    ② 凭空多出来的股数会随价格涨跌 ⇒ 每日扣 `剩余虚增股数 × 当日隐含价`；
        #    ③ 若这些股数**后来被卖出**，卖得的现金必须一并扣掉（否则增益被"实现"进现金里，
        #       终点不变 ⇒ 修正后年化假装没变化）。故维护 phantom_cash。
        dq_left = dq
        phantom_cash = 0.0
        q_prev = qb
        px_last = px
        for t in range(i, len(snaps)):
            qt = float((snaps[t].get("positions_qty") or {}).get(h["code"]) or 0.0)
            wt = float((snaps[t].get("weights") or {}).get(h["code"]) or 0.0)
            tv = float(snaps[t].get("total_value") or 0.0)
            if qt > 0 and wt:
                px_last = wt * tv / qt
            elif qt > 0:
                px_last = px_last
            if qt < q_prev and dq_left > 0:          # 当日有卖出：虚增股数优先被卖掉
                sold = q_prev - qt
                take = min(sold, dq_left)
                phantom_cash += take * px_last
                dq_left -= take
            q_prev = max(qt, 0.0)
            if dq_left > 0:
                cut[t] += dq_left * px_last
            cut[t] += phantom_cash
        recs.append({"code": h["code"], "ex_date": h["ex_date"], "stk_div": h["stk_div"],
                     "qty_before": qa, "qty_after": qb, "dq": dq,
                     "px": round(px, 4), "gain": round(gain, 2),
                     "nav_before": round(float(nav[i - 1]), 2),
                     "gain_pct_of_nav": round(gain / float(nav[i - 1]) if nav[i - 1] else 0.0, 6),
                     "held_at_end": bool(float((snaps[-1].get("positions_qty") or {})
                                               .get(h["code"]) or 0.0) > 0)})
    if not recs:
        return {"events": [], "has_hit": False}
    adj = np.maximum(nav - cut, 0.0)
    r0 = np.diff(nav) / nav[:-1]
    r1 = np.diff(adj) / adj[:-1]
    dd0 = nav / np.maximum.accumulate(nav) - 1
    dd1 = adj / np.maximum.accumulate(adj) - 1
    yrs = (len(nav) - 1) / 252.0
    return {
        "events": recs,
        "has_hit": True,
        "total_gain": round(float(sum(x["gain"] for x in recs)), 2),   # 除权日当天的账面增益
        "cum_cut_end": round(float(cut[-1]), 2),                       # 末日仍在扣的金额（逐日口径）
        "final_before": round(float(nav[-1]), 2),
        "final_after": round(float(adj[-1]), 2),
        "ann_before": float((nav[-1] / nav[0]) ** (1 / yrs) - 1),
        "ann_after": float((adj[-1] / adj[0]) ** (1 / yrs) - 1),
        "vol_before": float(r0.std(ddof=1) * np.sqrt(252)),
        "vol_after": float(r1.std(ddof=1) * np.sqrt(252)),
        "mdd_before": float(dd0.min()),
        "mdd_after": float(dd1.min()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="送转股与 adj_factor 不一致的凭空增益审计（P14）")
    ap.add_argument("--run")
    ap.add_argument("--all-runs")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    ev = build_events(force=a.rebuild)
    risk = {(r.code, r.ex_date): float(r.stk_div) for r in ev.itertuples(index=False)}
    print(f"[风险集] 送转>0 且 adj_factor 未变：{len(risk)} 个 (code, ex_date) / "
          f"{ev.code.nunique()} 只（cache={os.path.basename(CACHE)}）")
    if a.events:
        print(ev.to_string(index=False))
        return 0

    if a.run:
        hits = run_hits(a.run, risk)
        res = quantify(a.run, hits)
        print(f"\n## {os.path.basename(a.run)}  命中事件 {len(hits)} 个")
        if not res["has_hit"]:
            print("  结论：无凭空增益（未持有相关标的 / 未加股）")
            return 0
        for x in res["events"]:
            print(f"  {x['code']} ex={x['ex_date']} 送转/股={x['stk_div']} "
                  f"{'持有到末日' if x['held_at_end'] else '中途已卖出'} "
                  f"qty {x['qty_before']:,.0f}→{x['qty_after']:,.0f} ×{x['px']} "
                  f"= **{x['gain']:,.2f}** 元（占前一日净值 {x['gain_pct_of_nav']*100:.2f}%）")
        print(f"  除权日账面增益合计 {res['total_gain']:,.2f} 元｜末日仍在扣 {res['cum_cut_end']:,.2f} 元"
              f"｜终值 {res['final_before']:,.2f} → {res['final_after']:,.2f}")
        print(f"  年化 {res['ann_before']:+.2%} → **{res['ann_after']:+.2%}**"
              f"（Δ{(res['ann_after']-res['ann_before'])*100:+.2f}pp）｜"
              f"MDD {res['mdd_before']:.2%} → {res['mdd_after']:.2%}｜"
              f"波动 {res['vol_before']:.2%} → {res['vol_after']:.2%}")
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        return 1

    if a.all_runs:
        rows = []
        for k, rd in enumerate(find_runs(a.all_runs), 1):
            try:
                hits = run_hits(rd, risk)
                if not hits:
                    continue
                res = quantify(rd, hits)
                if not res.get("has_hit"):
                    continue
                rows.append(dict(run=os.path.basename(rd), run_dir=rd, n_events=len(res["events"]),
                                 total_gain=res["total_gain"],
                                 ann_before=res["ann_before"], ann_after=res["ann_after"],
                                 mdd_before=res["mdd_before"], mdd_after=res["mdd_after"]))
            except Exception as e:
                print(f"  [跳过] {os.path.basename(rd)}: {type(e).__name__}: {e}")
            if k % 100 == 0:
                print(f"  …已扫 {k}", flush=True)
        if not rows:
            print("\n无命中")
            return 0
        df = pd.DataFrame(rows).sort_values("total_gain", ascending=False)
        print(f"\n=== 命中 {len(df)} 个 run ===")
        for r in df.itertuples(index=False):
            print(f"  {r.run:<30}事件{r.n_events:>3}  凭空增益 {r.total_gain:>14,.0f}  "
                  f"年化 {r.ann_before:+.2%} → {r.ann_after:+.2%}"
                  f"（Δ{(r.ann_after-r.ann_before)*100:+.2f}pp）  MDD {r.mdd_before:.2%} → {r.mdd_after:.2%}")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stkdiv_hits_20261021.csv")
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n[csv] {out}")
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
