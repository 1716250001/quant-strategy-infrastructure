# -*- coding: utf-8 -*-
"""align_mdcore —— BTF 数据层 vs 赤潮/md_core 口径对齐抽检（PoC-1 任务 1.9）。

前提：两侧指向同一主库 D:\\全量数据\\market_data（md_core paths.BASE ==
btf MARKET_DATA_DIR，USE_ONLINE_API=False 本地模式），差异只能来自
读取语义/单位换算——这正是本抽检要暴露的。

抽检矩阵：
    A. daily 单标的 4 只（沪深主板/创业板/科创板）+ 端点小区间（疫情停牌段）：
       行集（含坏行剔除语义）/日期升序/OHLC·pre_close 逐值相等/
       Bar.volume==vol×100（手→股）/Bar.amount==amount×1000（千元→元）；
    B. fund_daily（ETF 510300.SH）：同 A；
    C. adj_factor / stk_limit 原始值（无量纲，1.4 契约无换算）逐行对齐；
    D. 截面行数与代码集：get_market_daily(单日) vs iter_day_slices 日区间。

已知语义差异（非 bug，登记备查）：
    - md_core 本地分支 get_adj_factor/get_stk_limit 忽略 start/end 参数
      （返回全历史，本抽检脚本内自行过滤区间）；
    - md_core 不过滤 OHLC 空值坏行，BTF Feed 剔除（05 §20.4 质量门）——
      A/B 项按「md_core 好行」对齐；
    - md_core 原始口径 vol（手）/amount（千元），BTF 契约口径已换算。

判据：全部 PASS 退出码 0；任一 FAIL 退出码 1（可挂 CI）。
"""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"d:\量化策略\代码\btf")  # btf 包
sys.path.insert(0, r"d:\量化策略\赤潮")  # md_core 包

import pandas as pd
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import TushareParquetFeed
from btf.data.parquet_reader import iter_day_slices, read_codes
from btf.domain.types import TradingDate
from md_core.quotes import (
    get_adj_factor,
    get_daily,
    get_fund_daily,
    get_market_daily,
    get_stk_limit,
)

ROOT = Path(MARKET_DATA_DIR)
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def td(s: str) -> TradingDate:
    return TradingDate(date(int(s[:4]), int(s[4:6]), int(s[6:8])))


def _num_eq(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


feed = TushareParquetFeed(ROOT)

# ─────────────────────────────────────────────
# A/B. daily / fund_daily 单标的对齐
# ─────────────────────────────────────────────
CASES = [
    ("daily", "000001.SZ", "20150101", "20241231"),
    ("daily", "600519.SH", "20150101", "20241231"),
    ("daily", "300750.SZ", "20180601", "20241231"),
    ("daily", "688981.SH", "20200701", "20241231"),
    ("daily", "000001.SZ", "20200203", "20200331"),  # 端点小区间（疫情停牌段）
    ("fund_daily", "510300.SH", "20150101", "20241231"),
]


def align_bars(table: str, ts: str, s: str, e: str) -> None:
    tag = f"{table.upper()}/{ts}/{s[:4]}..{e[:4]}"
    bars = feed.bars_of(ts, td(s), td(e), daily_table=table)
    raw = (get_daily if table == "daily" else get_fund_daily)(ts, start=s, end=e)
    raw = raw[raw["trade_date"].between(s, e)].reset_index(drop=True)
    good = raw.dropna(subset=["open", "high", "low", "close", "pre_close"]).reset_index(drop=True)

    check(
        f"{tag} 行数（Bar vs md_core 好行）",
        len(bars) == len(good),
        f"{len(bars)} vs {len(good)}（md_core 原始 {len(raw)}，坏行 {len(raw) - len(good)}）",
    )
    if not bars or good.empty:
        return

    n_bad_date = n_bad_px = n_bad_vol = n_bad_amt = 0
    for i, b in enumerate(bars):
        r = good.iloc[i]
        if b.date.to_ymd() != str(r["trade_date"]):
            n_bad_date += 1
            continue
        if any(getattr(b, f) != float(r[f]) for f in ("open", "high", "low", "close", "pre_close")):
            n_bad_px += 1
        v, a = r["vol"], r["amount"]
        if pd.isna(v):
            if b.volume != 0.0:
                n_bad_vol += 1
        elif not _num_eq(b.volume, float(v) * 100.0):
            n_bad_vol += 1
        if pd.isna(a):
            if b.amount != 0.0:
                n_bad_amt += 1
        elif not _num_eq(b.amount, float(a) * 1000.0):
            n_bad_amt += 1

    dates_sorted = [b.date.to_ymd() for b in bars] == sorted(b.date.to_ymd() for b in bars)
    check(f"{tag} 日期升序", dates_sorted)
    check(f"{tag} 日期错位", n_bad_date == 0, f"{n_bad_date} 行")
    check(f"{tag} OHLC/pre_close 逐值", n_bad_px == 0, f"{n_bad_px} 行不一致")
    check(f"{tag} vol×100==volume", n_bad_vol == 0, f"{n_bad_vol} 行不一致")
    check(f"{tag} amount×1000==amount", n_bad_amt == 0, f"{n_bad_amt} 行不一致")


for table, ts, s, e in CASES:
    align_bars(table, ts, s, e)

# ─────────────────────────────────────────────
# C. adj_factor / stk_limit 原始值（无量纲）
# ─────────────────────────────────────────────
S, E = "20150101", "20241231"
TS = "000001.SZ"


def align_raw(table: str, value_cols: list[str], md_df: pd.DataFrame) -> None:
    btf_df = read_codes(table, ROOT, [TS], S, E).to_pandas()
    md = md_df[md_df["trade_date"].between(S, E)].sort_values("trade_date").reset_index(drop=True)
    btf_df = btf_df.sort_values("trade_date").reset_index(drop=True)
    tag = f"RAW/{table}/{TS}"
    check(f"{tag} 行数", len(btf_df) == len(md), f"{len(btf_df)} vs {len(md)}")
    if btf_df.empty or md.empty or len(btf_df) != len(md):
        return
    bad = 0
    for c in value_cols:
        for i in range(len(md)):
            if not _num_eq(float(btf_df.iloc[i][c]), float(md.iloc[i][c])):
                bad += 1
                break
    check(f"{tag} 值逐行对齐（{'+'.join(value_cols)}）", bad == 0, f"{bad} 个标的不一致")


align_raw("adj_factor", ["adj_factor"], get_adj_factor(TS))
align_raw("stk_limit", ["up_limit", "down_limit"], get_stk_limit(TS))

# ─────────────────────────────────────────────
# D. 截面行数与代码集（get_market_daily vs iter_day_slices）
# ─────────────────────────────────────────────
for d in ("20150612", "20200310", "20240930"):
    md = get_market_daily(d, d)
    btf_rows: list[str] = []
    for ymd, cols, lo, hi in iter_day_slices(
        "daily", ROOT, d, d, columns=["ts_code", "trade_date"]
    ):
        btf_rows = cols["ts_code"][lo:hi]
    md_codes = set(md["ts_code"]) if not md.empty else set()
    check(
        f"截面/{d} 行数", len(btf_rows) == len(md),
        f"{len(btf_rows)} vs {len(md)}",
    )
    check(
        f"截面/{d} 代码集", set(btf_rows) == md_codes,
        f"差集 {len(set(btf_rows) ^ md_codes)} 个",
    )

print("\n" + "=" * 60)
print(f"对齐抽检结论：{'全部一致 ✓' if not FAILURES else f'{len(FAILURES)} 项不一致 ✗'}")
for f in FAILURES:
    print(f"  ✗ {f}")
sys.exit(0 if not FAILURES else 1)
