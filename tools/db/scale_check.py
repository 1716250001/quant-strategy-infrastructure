#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/db/scale_check.py — 全库数值域量级锚体检（R11-b，**只读**）
====================================================================
三层：
  L1 绝对量级带  `config/scale_anchors.py` 的 `{表:{列:{lo,hi,why}}}`，越界逐条列示；
  L2 跨表勾稽    3 对（total_mv↔total_share×close／pe_ttm×n_income↔total_mv／
                 fund_daily.amount↔vol×close），相对误差 >1% 列示；
  L3 时序稳定性  同列月度中位数环比 |Δ|>50% **仅提示**，不定性、不改退出码。

纪律：
  · **只读**：不写 `D:\\全量数据` 任何文件；不缓存、不落中间件；
  · **不进 `n_issue`**、不改 `db audit` 退出码（诊断项，与 truncation 同待遇）；
  · 锚点提案稿须赤潮逐行复核后才准全库跑；本脚本本身不判断阈值对错。
  · `--stats` 复算 0.1/99.9 分位分片包络（`why` 里引用的就是它），幂等。

用法：
    python tools/db/scale_check.py --report                 # L1+L2+L3 全跑
    python tools/db/scale_check.py --report --tables market_data/daily,daily_basic
    python tools/db/scale_check.py --stats                  # 复算分位（供复核）
    python tools/db/scale_check.py --stats --json out.json
退出码：0=跑完（无论有无越界）/ 2=数据读取致命错误
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config
from config.scale_anchors import SCALE_ANCHORS

ROOT = Path(config.DATA_ROOT)
L2_THRESHOLD = 0.01          # 1%
L3_THRESHOLD = 0.50          # 月环比 50%


def _paths(table: str) -> list[Path]:
    d = ROOT / table
    return sorted(d.glob("*.parquet")) if d.is_dir() else []


def _id_cols(schema: list[str]) -> list[str]:
    return [c for c in ("ts_code", "trade_date", "end_date", "ann_date") if c in schema]


def _stats_table(table: str, col: str) -> dict:
    """分片包络：q001 取各分片最小、q999 取最大（与锚点生成脚本同口径）。"""
    lo = hi = None
    n = 0
    used = 0
    for p in _paths(table):
        try:
            t = pq.read_table(p, columns=[col])
        except Exception:
            continue
        arr = t.column(col).combine_chunks().drop_null()
        n += len(arr)
        if len(arr) == 0:
            continue
        try:
            q = pc.quantile(arr, q=[0.001, 0.999], interpolation="linear")
            a, b = float(q[0].as_py()), float(q[1].as_py())
        except Exception:
            continue
        used += 1
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    return {"table": table, "col": col, "q001": lo, "q999": hi, "n": n, "shards": used}


def cmd_stats(tables: list[str] | None, out: str | None) -> int:
    rows = []
    for table, cols in sorted(SCALE_ANCHORS.items()):
        if tables and table not in tables:
            continue
        for col in cols:
            rows.append(_stats_table(table, col))
    for r in rows:
        if r["q001"] is None:
            print(f"[!] {r['table']}.{r['col']} 无数据（shards={r['shards']}）")
        else:
            print(f"{r['table']}\t{r['col']}\tq001={r['q001']:.6g}\tq999={r['q999']:.6g}"
                  f"\tn={r['n']}\tshards={r['shards']}")
    if out:
        Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[OK] 写入 {out}")
    return 0


def _scan_l1(table: str, col: str, lo: float, hi: float, limit: int = 5) -> dict:
    """逐分片只读扫描，累计越界条数并留样（不排序全表，内存有界）。"""
    n_low = n_high = 0
    samples: list[dict] = []
    for p in _paths(table):
        try:
            sch = pq.read_schema(p).names
            cols = [col, *_id_cols(sch)]
            df = pq.read_table(p, columns=cols).to_pandas()
        except Exception:
            continue
        if col not in df:
            continue
        s = df[col]
        low_mask = s.notna() & (s < lo)
        high_mask = s.notna() & (s > hi)
        n_low += int(low_mask.sum())
        n_high += int(high_mask.sum())
        if len(samples) < limit:
            take = df[low_mask | high_mask].head(limit - len(samples))
            for _, r in take.iterrows():
                samples.append({"file": p.name,
                                **{k: (None if r[k] is None else str(r[k]))
                                   for k in _id_cols(sch)},
                                "value": float(r[col])})
    return {"n_low": n_low, "n_high": n_high, "samples": samples}


def _latest(table: str, cols: list[str]) -> object | None:
    """最后一个分片（最新年份）按 ts_code 取最新一行（只读）。"""
    files = _paths(table)
    if not files:
        return None
    for p in reversed(files):
        try:
            sch = pq.read_schema(p).names
            use = [c for c in cols if c in sch] + [c for c in _id_cols(sch) if c not in cols]
            df = pq.read_table(p, columns=use).to_pandas()
        except Exception:
            continue
        if df.empty:
            continue
        dcol = "trade_date" if "trade_date" in df else ("end_date" if "end_date" in df else None)
        if dcol and "ts_code" in df:
            df = df.sort_values(dcol).drop_duplicates("ts_code", keep="last")
        return df
    return None


def _ttm_income():
    """按报告期推导 TTM 归母净利：最近年报 + 本期累计 − 上年同期累计（单季报产量级）。"""
    import pandas as pd

    frames = []
    for p in _paths("market_data/income"):
        sch = pq.read_schema(p).names
        if not {"ts_code", "end_date", "n_income"} <= set(sch):
            continue
        try:
            frames.append(pq.read_table(p, columns=["ts_code", "end_date", "n_income"]).to_pandas())
        except Exception:
            continue
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).dropna(subset=["end_date", "n_income"])
    df = df.sort_values(["ts_code", "end_date"]).drop_duplicates(["ts_code", "end_date"], keep="last")
    rows = []
    for code, g in df.groupby("ts_code", sort=False):
        last = g.iloc[-1]
        end = str(last["end_date"])
        if end[4:] == "1231":
            ttm = float(last["n_income"])
        else:
            md = end[4:]
            annual = g[g["end_date"].astype(str).str.endswith("1231")
                       & (g["end_date"].astype(str) < end)]
            prev = g[(g["end_date"].astype(str).str[4:] == md)
                     & (g["end_date"].astype(str).str[:4] == str(int(end[:4]) - 1))]
            if annual.empty or prev.empty:
                continue
            ttm = (float(annual.iloc[-1]["n_income"]) + float(last["n_income"])
                   - float(prev.iloc[-1]["n_income"]))
        rows.append({"ts_code": code, "n_income_ttm": ttm, "end_date": end})
    return pd.DataFrame(rows)


def cmd_l2() -> list[dict]:
    out: list[dict] = []

    db = _latest("market_data/daily_basic", ["ts_code", "total_mv", "pe_ttm", "close"])
    bs = _latest("market_data/balancesheet", ["ts_code", "total_share"])
    inc = _ttm_income()
    fd = _latest("market_data/fund_daily", ["ts_code", "amount", "vol", "close"])

    if db is not None and bs is not None:
        m = db.merge(bs, on="ts_code", suffixes=("", "_bs")).dropna(
            subset=["total_mv", "total_share", "close"])
        # total_share 股 × close 元 / 1e4 → 万元
        pred = m["total_share"] * m["close"] / 1e4
        rel = ((pred - m["total_mv"]).abs() / m["total_mv"].replace(0, float("nan")))
        for _, r in m[rel > L2_THRESHOLD].assign(rel=rel).nlargest(10, "rel").iterrows():
            out.append({"pair": "total_mv↔total_share×close", "ts_code": r["ts_code"],
                        "rel": float(r["rel"]), "a": float(r["total_mv"]), "b": float(pred.loc[r.name])})
        out.append({"pair": "total_mv↔total_share×close", "checked": len(m),
                    "over": int((rel > L2_THRESHOLD).sum()), "note": "单位：total_share 股×close 元/1e4=万元"})

    if db is not None and inc is not None:
        m = db.merge(inc, on="ts_code").dropna(subset=["pe_ttm", "n_income_ttm", "total_mv"])
        m = m[(m["pe_ttm"] > 0) & (m["total_mv"] > 0)]
        pred = m["pe_ttm"] * m["n_income_ttm"] / 1e4   # pe_ttm(倍) × TTM 净利(元)/1e4 → 万元
        rel = ((pred - m["total_mv"]).abs() / m["total_mv"].replace(0, float("nan")))
        for _, r in m[rel > L2_THRESHOLD].assign(rel=rel).nlargest(10, "rel").iterrows():
            out.append({"pair": "pe_ttm×n_income_ttm↔total_mv", "ts_code": r["ts_code"],
                        "rel": float(r["rel"]), "a": float(r["total_mv"]), "b": float(pred.loc[r.name])})
        out.append({"pair": "pe_ttm×n_income_ttm↔total_mv", "checked": len(m),
                    "over": int((rel > L2_THRESHOLD).sum()),
                    "note": "单位：TTM 净利 元；pe_ttm 倍；/1e4→万元（仅 pe_ttm>0）"})

    if fd is not None:
        m = fd.dropna(subset=["amount", "vol", "close"])
        # amount 千元；vol 手 ×100 份/手 × close 元 /1000 = 千元
        pred = m["vol"] * 100 * m["close"] / 1000
        rel = ((pred - m["amount"]).abs() / m["amount"].replace(0, float("nan")))
        for _, r in m[rel > L2_THRESHOLD].assign(rel=rel).nlargest(10, "rel").iterrows():
            out.append({"pair": "fund_daily.amount↔vol×close", "ts_code": r["ts_code"],
                        "rel": float(r["rel"]), "a": float(r["amount"]), "b": float(pred.loc[r.name])})
        out.append({"pair": "fund_daily.amount↔vol×close", "checked": len(m),
                    "over": int((rel > L2_THRESHOLD).sum()),
                    "note": "单位：amount 千元；vol 手×100×close/1000"})
    return out


def cmd_l3(tables: list[str] | None) -> list[dict]:
    """同列月度中位数环比 >50% 仅提示（按 ts_code 全市场合并后的月度中位数）。"""
    out: list[dict] = []
    for table, cols in sorted(SCALE_ANCHORS.items()):
        if tables and table not in tables:
            continue
        for col in cols:
            frames = []
            for p in _paths(table):
                sch = pq.read_schema(p).names
                if "trade_date" not in sch or col not in sch:
                    continue
                try:
                    frames.append(pq.read_table(p, columns=["trade_date", col]).to_pandas())
                except Exception:
                    continue
            if not frames:
                continue
            import pandas as pd

            df = pd.concat(frames, ignore_index=True).dropna()
            if df.empty:
                continue
            df["ym"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d",
                                      errors="coerce").dt.to_period("M")
            med = df.groupby("ym")[col].median().sort_index()
            if len(med) < 3:
                continue
            chg = med.pct_change().abs()
            hits = chg[chg > L3_THRESHOLD]
            if len(hits):
                out.append({"table": table, "col": col, "months": len(med),
                            "hits": [(str(k), float(v)) for k, v in hits.items()][:5]})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="数值域量级锚体检（只读）")
    ap.add_argument("--report", action="store_true", help="L1+L2+L3 全跑（默认）")
    ap.add_argument("--stats", action="store_true", help="复算 0.1/99.9 分位分片包络")
    ap.add_argument("--tables", default=None, help="仅指定表（逗号分隔，完整相对路径）")
    ap.add_argument("--json", default=None, help="--stats 结果另存 JSON")
    ap.add_argument("--out", default=None, help="--report 结果另存 Markdown")
    args = ap.parse_args(argv)
    tables = [t.strip() for t in args.tables.split(",") if t.strip()] if args.tables else None

    if args.stats:
        return cmd_stats(tables, args.json)

    lines = ["# 数据库数值域体检（scale_check）", ""]
    total = viol = 0
    for table, cols in sorted(SCALE_ANCHORS.items()):
        if tables and table not in tables:
            continue
        for col, a in cols.items():
            total += 1
            r = _scan_l1(table, col, a["lo"], a["hi"])
            n = r["n_low"] + r["n_high"]
            if n:
                viol += 1
                lines.append(f"- [L1] `{table}.{col}` 越界 {n} 条"
                             f"（低 {r['n_low']}／高 {r['n_high']}，带 [{a['lo']}, {a['hi']}]）")
                for s in r["samples"]:
                    lines.append(f"    - 样: {s}")
    lines.append(f"\n[L1] 列 {total}｜越界列 {viol}")

    l2 = cmd_l2()
    lines.append("\n## L2 跨表勾稽（相对误差 >1% 列示）\n")
    for r in l2:
        if "checked" in r:
            lines.append(f"- `{r['pair']}` 检查 {r['checked']} 只｜>1% {r['over']} 只｜{r['note']}")
        else:
            lines.append(f"    - {r['ts_code']} 相对误差 {r['rel']:.4%}：a={r['a']:.6g} b={r['b']:.6g}")

    l3 = cmd_l3(tables)
    lines.append("\n## L3 月度中位数环比 |Δ|>50%（仅提示，不定性）\n")
    if not l3:
        lines.append("- 无")
    for r in l3:
        lines.append(f"- `{r['table']}.{r['col']}` 月数 {r['months']}｜命中 {r['hits']}")

    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"[OK] 写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
