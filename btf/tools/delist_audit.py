# -*- coding: utf-8 -*-
"""delist_audit.py — btf run 的「退市残仓」审计与口径修正（本地工具，不改 btf 核心）

背景（2026-10-21 实测）
    btf 的 `tradability` 对退市标的同时拒买卖 ⇒ 退市前买入的仓位**永远卖不掉**，成为
    「僵尸仓」，按末次收盘价计入净值直到区间末。平台默认 `delisted_settlement=carry`，

    核销起点边界（2026-10-21 实测，**年化对边界不敏感、MDD 可能敏感**）：
      - 默认用 `delist_date`：从退市日起逐日扣残值；
      - 若改用「该股末次 bar 的次一交易日」：**终值/年化逐位相同**（差 0.00bp），
        **MDD 通常也相同**（db753c / a41265 / 39a9e4 实测 0.00pp），但当净值谷值
        落在两者之间的空窗时会有差异（66f207 实测 −85.36% → −86.20%，差 0.84pp，
        谷值日 2024-09-18 → 2024-07-25）。引用 MDD 时须注明所用边界。

    平台默认 `delisted_settlement=carry`，
    可切 `writeoff`，但 `_writeoff_settlement` **只作用于末日快照**（docstring 明写）。
    后果：① 末日净值含僵尸残值（若占比重会严重高估）；② 损失时点错位（记在末日而非退市日）；
          ③ 僵尸仓占名义权重，"平均仓位"失真；④ 每天产生一条 `risk:delisted` 拒单。

本工具做的事（**只读 run 产物，不改任何东西**）
    1. 识别全部「在持且已退市」记录（stock_basic.delist_date ≤ 在持日）；
    2. 单列 `zombie_weight`（占净值比例）的均值/最大/末日值；
    3. 统计 `risk:delisted` 拒单；
    4. 按 **退市日写零** 重建净值路径（counterfactual），重算年化/波动/回撤，
       与平台 metrics（carry）、writeoff 臂并列 —— 给出「正确时点」的回撤；
    5. `--all-runs` 扫描整个产物目录，输出一张汇总表。

用法
    python delist_audit.py --run <run_dir>
    python delist_audit.py --all-runs <runs_root> [--top 15]
    python delist_audit.py --run <run_dir> --json
"""
from __future__ import annotations

import argparse
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

DEFAULT_SB = str(MARKET_DATA_DIR / "stock_basic.parquet")


# ---------------------------------------------------------------- 读产物
def load_run(run_dir: str) -> dict:
    out = {"run_dir": run_dir}
    with open(os.path.join(run_dir, "metrics.json"), encoding="utf-8") as fh:
        out["metrics"] = json.load(fh).get("metrics", {})
    with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as fh:
        out["manifest"] = json.load(fh)
    snap = []
    with open(os.path.join(run_dir, "snapshots.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            snap.append({"date": int(str(r["date"]).replace("-", "")),
                         "total_value": r["total_value"],
                         "cash": r["cash"],
                         "market_value": r["market_value"],
                         "qty": r.get("positions_qty") or {},
                         "weights": r.get("weights") or {}})
    out["snap"] = pd.DataFrame(snap).sort_values("date").reset_index(drop=True)
    rej = []
    p = os.path.join(run_dir, "rejections.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            rej = [json.loads(x) for x in fh]
    out["rej"] = rej
    return out


def delist_map(sb_path: str) -> dict[str, int]:
    sb = pd.read_parquet(sb_path, columns=["ts_code", "name", "list_status", "delist_date"])
    sb = sb[sb["delist_date"].notna()]
    out = {}
    for code, name, dd in zip(sb["ts_code"], sb["name"], sb["delist_date"]):
        s = str(dd).strip()
        if len(s) >= 8 and s[:8].isdigit():
            out[str(code)] = int(s[:8])
    return out


# ---------------------------------------------------------------- 审计
def audit(run: dict, dmap: dict[str, int], sb_path: str) -> dict:
    df = run["snap"].copy()
    # 每一行里「已退市且仍在持」的标的
    rows = []
    for rec in df.itertuples(index=False):
        for code, q in (rec.qty or {}).items():
            if q and q > 0 and code in dmap and dmap[code] <= rec.date:
                # 引擎计入该仓的市值：weights × total_value（@CASH 除外）
                w = (rec.weights or {}).get(code)
                mv = float(w) * rec.total_value if w is not None else np.nan
                rows.append({"date": rec.date, "code": code, "qty": q,
                             "mv": mv, "total_value": rec.total_value})
    zdf = pd.DataFrame(rows)
    res = {"run_dir": run["run_dir"],
           "metrics": {k: run["metrics"].get(k) for k in
                       ("total_return", "annualized_return", "annualized_volatility",
                        "sharpe_ratio", "max_drawdown", "n_fills", "total_fees",
                        "turnover_annualized")},
           "n_snapshots": len(df),
           "period": [int(df["date"].iloc[0]), int(df["date"].iloc[-1])] if len(df) else [],
           "exposure_mean": float((df["market_value"] / df["total_value"]).mean()) if len(df) else None,
           "has_zombie": bool(len(zdf))}

    # 拒单
    dl = [r for r in run["rej"] if "delisted" in str(r.get("message", ""))]
    res["delisted_rejections"] = len(dl)
    res["delisted_reject_symbols"] = sorted({r["order"]["symbol"] for r in dl}) if dl else []
    if dl:
        ds = [str(r["order"]["created_at"]) for r in dl]
        res["delisted_reject_range"] = [min(ds), max(ds)]

    if not len(zdf):
        res["verdict"] = "无退市残仓"
        return res

    # 汇总每个僵尸仓
    zdf["year"] = zdf["date"] // 10000
    g = zdf.groupby("code").agg(first=("date", "min"), last=("date", "max"),
                                qty_max=("qty", "max"), mv=("mv", "max"))
    g["delist_date"] = [dmap.get(c) for c in g.index]
    res["zombies"] = g.reset_index().to_dict("records")

    # zombie_weight 时间序列（占净值）
    w_ts = zdf.groupby("date").apply(
        lambda x: x["mv"].sum() / x["total_value"].iloc[0], include_groups=False)
    res["zombie_weight"] = {"mean": float(w_ts.mean()), "max": float(w_ts.max()),
                            "end": float(w_ts.iloc[-1]), "n_days": len(w_ts)}

    # 末日残值
    last = df.iloc[-1]
    end_mv = 0.0
    for code, q in (last["qty"] or {}).items():
        if code in dmap and dmap[code] <= last["date"] and q > 0:
            w = (last["weights"] or {}).get(code)
            if w is not None:
                end_mv += float(w) * last["total_value"]
    res["end_zombie_value"] = end_mv
    res["end_zombie_weight"] = float(end_mv / last["total_value"]) if last["total_value"] else np.nan

    # ---- 修正口径：按退市日写零 ----
    # 用**逐日**僵尸残值（来自该日 weights×total_value），而不是常数：
    # 这样对 carry（末日仍含残值）与 writeoff（仅末日核销）两种产物都能得到同一条"退市日归零"路径。
    zv = zdf.groupby("date")["mv"].sum().rename("zv")
    corr = df.merge(zv, left_on="date", right_index=True, how="left")
    corr["zv"] = corr["zv"].fillna(0.0)
    detail = []
    for code, rec in g.iterrows():
        dd = dmap.get(code)
        if dd is None:
            continue
        detail.append({"code": code, "delist_date": dd,
                       "value_at_close": float(rec["mv"]) if np.isfinite(rec["mv"]) else 0.0,
                       "days_carried": int(zdf[zdf["code"] == code]["date"].nunique())})
    corr["nav"] = corr["total_value"] - corr["zv"]
    nav = corr["nav"].to_numpy(float)
    nav = np.maximum(nav, 0.0)
    d = corr["date"].to_numpy()
    # 年化口径与平台一致：**(n-1)/252**
    #   2026-10-21 红队实测：用 3 个 run 反解，平台 annualized_return 与 (n-1)/252 差 0.0000bp，
    #   与 n/252 差 0.20–0.79bp。原实现用 n/252 ⇒ 修正年化系统性高约 0.002–0.008pp。
    yrs = (len(nav) - 1) / 252.0
    r = np.diff(nav) / nav[:-1]
    res["corrected"] = {
        "detail": detail,
        "final_value": float(nav[-1]),
        "total_return": float(nav[-1] / nav[0] - 1),
        "annualized_return": float((nav[-1] / nav[0]) ** (1 / yrs) - 1),
        "annualized_volatility": float(r.std(ddof=1) * np.sqrt(252)) if len(r) > 2 else None,
        "max_drawdown": float((nav / np.maximum.accumulate(nav) - 1).min()),
        "years": round(float(yrs), 2),
    }
    # 平台口径（原路径）回撤，便于对比
    v0 = df["total_value"].to_numpy(float)
    res["platform_path_mdd"] = float((v0 / np.maximum.accumulate(v0) - 1).min())
    # 损失时点诊断：原路径单日最大跌幅发生在哪天、幅度多少
    if len(v0) > 2:
        dr = np.diff(v0) / v0[:-1]
        k = int(np.argmin(dr))
        res["worst_day"] = {"date": int(d[k + 1]), "ret": float(dr[k])}
        res["worst_day_is_last"] = bool(k == len(dr) - 1)
    res["verdict"] = "存在退市残仓（须并报修正口径）"
    return res


# ---------------------------------------------------------------- 输出
def fmt_one(r: dict) -> str:
    L = []
    m, p = r["metrics"], r["period"]
    L.append(f"## {os.path.basename(r['run_dir'])}")
    L.append(f"- 区间 {p[0]}~{p[1]}（{r['n_snapshots']} 个交易日）｜平均仓位 {r['exposure_mean']*100:.1f}%")
    L.append(f"- 平台 metrics：年化 {m['annualized_return']*100:+.2f}%｜"
             f"波动 {m['annualized_volatility']*100:.2f}%｜MDD {m['max_drawdown']*100:+.2f}%｜"
             f"total {m['total_return']*100:+.2f}%")
    L.append(f"- `risk:delisted` 拒单 **{r['delisted_rejections']}** 条"
             + (f"（{len(r['delisted_reject_symbols'])} 个标的：{r['delisted_reject_symbols']}，"
                f"{r['delisted_reject_range'][0]}~{r['delisted_reject_range'][1]}）"
                if r["delisted_rejections"] else ""))
    if not r["has_zombie"]:
        L.append("- 结论：**无退市残仓**，平台 metrics 可直接引用")
        return "\n".join(L)
    L.append(f"- ⚠️ 僵尸仓 **{len(r['zombies'])}** 只；`zombie_weight` 均值 "
             f"{r['zombie_weight']['mean']*100:.1f}%／最大 {r['zombie_weight']['max']*100:.1f}%／"
             f"末日 {r['zombie_weight']['end']*100:.1f}%，末日残值 **{r['end_zombie_value']:,.0f}** 元")
    L.append("\n| 标的 | 退市日 | 末次在持 | 最大股数 | 计入残值(元) |")
    L.append("|:--|--:|--:|--:|--:|")
    for z in r["zombies"]:
        L.append(f"| {z['code']} | {z['delist_date']} | {z['last']} | {int(z['qty_max']):,} | "
                 f"{(z['mv'] if z['mv'] == z['mv'] else 0):,.0f} |")
    c = r["corrected"]
    L.append(f"\n**修正口径（按退市日写零）**：终值 **{c['final_value']:,.0f}** 元｜"
             f"total {c['total_return']*100:+.2f}%｜年化 **{c['annualized_return']*100:+.2f}%**｜"
             f"波动 {c['annualized_volatility']*100:.2f}%｜MDD **{c['max_drawdown']*100:+.2f}%**")
    L.append(f"- 平台原路径 MDD = {r['platform_path_mdd']*100:+.2f}%")
    wd = r.get("worst_day")
    if wd:
        L.append(f"- 原路径单日最大跌幅 {wd['ret']*100:+.2f}% 发生在 {wd['date']}"
                 + ("（**正是区间末日 ⇒ 核销时点错位**）" if r.get("worst_day_is_last") else "（不在末日）"))
    L.append(f"- 与平台年化之差 = {(c['annualized_return'] - m['annualized_return'])*100:+.2f}pp；"
             f"与平台 MDD 之差 = {(c['max_drawdown'] - m['max_drawdown'])*100:+.2f}pp")
    L.append("- 结论：**该 run 的指标须以修正口径为主，平台口径只作对照**")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="btf run 退市残仓审计（只读）")
    ap.add_argument("--run", default="")
    ap.add_argument("--all-runs", default="", help="扫描该目录下的每个子目录为一个 run")
    ap.add_argument("--stock-basic", default=DEFAULT_SB)
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    dmap = delist_map(a.stock_basic)
    print(f"[口径] stock_basic={a.stock_basic}（退市 {len(dmap)} 只）；只读审计，不改任何产物\n")

    if a.all_runs:
        rows = []
        for name in sorted(os.listdir(a.all_runs)):
            rd = os.path.join(a.all_runs, name)
            if not os.path.isdir(rd) or not os.path.exists(os.path.join(rd, "metrics.json")):
                continue
            r = audit(load_run(rd), dmap, a.stock_basic)
            rows.append({"run": name,
                         "区间": f"{r['period'][0]}" if r["period"] else "",
                         "年化%": round((r["metrics"]["annualized_return"] or 0) * 100, 2),
                         "MDD%": round((r["metrics"]["max_drawdown"] or 0) * 100, 2),
                         "僵尸仓": len(r.get("zombies") or []),
                         "zombie末日权重%": round((r.get("end_zombie_weight") or 0) * 100, 1),
                         "delisted拒单": r["delisted_rejections"],
                         "修正年化%": (round(r["corrected"]["annualized_return"] * 100, 2)
                                      if r.get("corrected") else None),
                         "修正MDD%": (round(r["corrected"]["max_drawdown"] * 100, 2)
                                     if r.get("corrected") else None)})
        t = pd.DataFrame(rows)
        pd.set_option("display.width", 240)
        print(t.to_string(index=False))
        if a.out:
            t.to_csv(a.out, index=False, encoding="utf-8-sig")
            print(f"\n[saved] {a.out}")
        return 0

    if not a.run:
        print("[错误] 需要 --run 或 --all-runs", file=sys.stderr)
        return 2
    r = audit(load_run(a.run), dmap, a.stock_basic)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    else:
        txt = fmt_one(r)
        print(txt)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(txt)
            print(f"\n[saved] {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
