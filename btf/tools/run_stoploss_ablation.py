# -*- coding: utf-8 -*-
"""止损 / 止盈**敏感性消融**（老大 2026-10-01 令 · 决策项 2）。

把 2026-08-21 v7.4 审计里**最有争议的那句结论**拆开重算：

    「选股 + 止损放宽的联合改善 = 从 0.77% 到 2.26% = 2.9 倍提升」
                                    （l2-selection-oos-report.md §5.4）

疑点：0.77%（策略K，止损 -3%）→ 1.07%（BASE，止损 -5%）这两格的**选股方式相同**，
差异纯粹来自**止损参数**；把它和后面的选股效应合并宣称为"2.9 倍"，是典型的
**参数漂移伪装成 alpha**。本工具用**正交设计**把两者分开：

    (selection) × (止损档) —— 同一次抽样、同一次数据加载、同一套退出规则，
    只让这两个变量动。于是：
      · BASE(-3%) vs BASE(-5%)          → **纯止损效应**（旧报告混掉的那部分）
      · BASE(-5%) vs 选股(-5%)          → **纯选股效应**
      · BASE(-3%) vs 选股(-5%)          → 旧报告宣称的"2.9 倍"总量

## 口径（忠实复刻 `l2_selection_oos.py`，可逐位对照）

| 项 | 取值 | 来源 |
|:---|:---|:---|
| 抽样股票数 | 500 | `N_STOCKS`；`RandomState(42)` 无放回抽样后 **sorted** |
| 每只入场数 | 上限 20 | `N_ENTRIES_PER_STOCK`；`RandomState(42*1000+si)` |
| 最长持有 | 60 交易日 | `MAX_HOLD_DAYS` |
| 止盈 | +20% 卖 30% | `TP1_TRIGGER/TP1_SELL_FRAC` |
| 移动止盈 | 高点回撤 5% 清仓（须已触发止盈） | `TRAILING_STOP` |
| 双边成本 | 0.30%（单边 0.15%） | `COST_SINGLE × 2` |
| 价格 | `close × adj_factor`（后复权） | daily + adj_factor |
| 仓位缩放 | HS300 MA20 之上 1.0 / 之下 0.5 | index_daily 000300.SH |

**必须披露的口径差异**（旧中间产物已不留存，改用同一真源重算）：

1. `limit_up_ratio`（L1 入场过滤 >1.8%）：旧脚本读
   `.context/audit-20260820/limit_up_ratio_daily_2008_2026.csv`（**该 CSV 已不存在**），
   此处由 `daily.pct_chg` 重算（主板 ≥9.8% / 创业板科创 ≥19.8% 判定涨停），
   近似必然带来数值差异；
   ⇒ **但 L1 只决定入场样本集，对同一 selection 下的所有止损档是同一份样本**，
     故**止损效应归因对该口径不敏感**，只影响绝对水平。
2. LIQ CRISIS 日：旧脚本读 `l0_liq_state_calibrated.csv`，此处用
   `btf.data.liq.state_of`（规则镜像口径）——真源，优于旧中间产物。
3. ROE PIT：用 `fina_indicator` 的 `ann_date ≤ 入场日`（与 btf 全链路同一口径）。
4. 跌停判定：旧脚本用 `security_state` 表（主库未见该表），此处用 `pct_chg` 近似。

## 用法

    python tools/run_stoploss_ablation.py                 # 全矩阵
    python tools/run_stoploss_ablation.py --quick         # 50 只样本冒烟
    python tools/run_stoploss_ablation.py --start 20160101 --end 20260923
退出码：0=完成 / 1=失败
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from btf.config.paths import MARKET_DATA_DIR, OUTPUT_DIR
from btf.data.core import YearTableStore

OUT_DEFAULT = OUTPUT_DIR / "stoploss-ablation"
MIRROR = Path(r"D:\量化策略\赤潮\rules_mirror_v77.json")

# ── 忠实复刻旧脚本常量 ──
SEED = 42
N_STOCKS = 500
N_ENTRIES_PER_STOCK = 20
MAX_HOLD_DAYS = 60
L1_GREEN_THRESHOLD = 1.8
TP1_TRIGGER = 1.20
TP1_SELL_FRAC = 0.30
TRAILING_STOP = 0.95
LIMIT_DOWN_MAX_RETRY = 5
COST_SINGLE = 0.0015
MA20_WINDOW = 20

#: 止损档（None = 不止损，仅时间/止盈退出）
STOP_LOSS_LEVELS: list[tuple[str, float | None]] = [
    ("sl_none", None), ("sl-2%", 0.98), ("sl-3%", 0.97),
    ("sl-4%", 0.96), ("sl-5%", 0.95), ("sl-7%", 0.93),
]
#: 选股方案（BASE = 旧脚本的无过滤基准）
SELECTIONS = ("BASE", "current", "pr")

#: A 股代码前缀白名单（与旧脚本 is_a_share 一致）
_A_PREFIX = {"000", "001", "002", "003", "300", "301",
             "600", "601", "603", "605", "688", "689", "920"}


def _is_a_share(code: str) -> bool:
    return code[:3] in _A_PREFIX and (
        code.endswith(".SZ") or code.endswith(".SH") or code.endswith(".BJ"))


def _is_chiyeban(code: str) -> bool:
    return code.startswith("300") or code.startswith("301")


def _limit_up_thr(code: str) -> float:
    """涨停阈值（%）：创业板/科创 20%，其余 10%。"""
    return 19.8 if (code.startswith("300") or code.startswith("301")
                    or code.startswith("688") or code.startswith("689")) else 9.8


def build_universe(store: YearTableStore, ref_year: int) -> list[str]:
    """A 股代码全集（以 `ref_year` 出现者为全集，排序确定）。"""
    t = store.read_file("daily", year=ref_year, columns=["ts_code"])
    if t is None:
        raise RuntimeError(f"主库缺 daily/{ref_year}.parquet")
    codes = sorted({str(c) for c in t.column("ts_code").to_pylist()
                    if _is_a_share(str(c))})
    return codes


def sample_codes(universe: list[str], n_stocks: int,
                 seed: int = SEED) -> list[str]:
    """无放回抽样后 **sorted**（与旧脚本 `sampled_idx` 处理一致）。"""
    rng = np.random.RandomState(seed)
    idx = rng.choice(len(universe), size=min(n_stocks, len(universe)),
                     replace=False)
    return [universe[i] for i in sorted(idx)]


def load_prices(store: YearTableStore, codes: list[str], start: str,
                end: str) -> dict[str, pd.DataFrame]:
    """加载后复权收盘序列 → {code: DataFrame[trade_date, adj_close, pct_chg]}。"""
    t0 = time.perf_counter()
    daily = store.read_codes("daily", codes, start, end,
                             columns=["ts_code", "trade_date", "close",
                                      "pct_chg"]).to_pandas()
    adj = store.read_codes("adj_factor", codes, start, end,
                           columns=["ts_code", "trade_date",
                                    "adj_factor"]).to_pandas()
    if daily.empty:
        raise RuntimeError("daily 区间内无数据")
    daily["trade_date"] = daily["trade_date"].astype(str)
    daily["adj_factor"] = 1.0
    if not adj.empty:
        adj["trade_date"] = adj["trade_date"].astype(str)
        daily = daily.merge(adj[["ts_code", "trade_date", "adj_factor"]],
                            on=["ts_code", "trade_date"], how="left")
        daily["adj_factor"] = daily["adj_factor_y"].fillna(
            daily["adj_factor_x"] if "adj_factor_x" in daily else 1.0)
    daily["adj_close"] = daily["close"] * daily["adj_factor"]
    out: dict[str, pd.DataFrame] = {}
    for code, g in daily.groupby("ts_code", sort=True):
        g = g.sort_values("trade_date").reset_index(drop=True)
        out[str(code)] = g[["trade_date", "adj_close", "pct_chg"]]
    print(f"    价格加载 {len(out)} 只 / {time.perf_counter()-t0:.1f}s")
    return out


def build_limit_up_ratio(prices: dict[str, pd.DataFrame]) -> dict[str, float]:
    """涨停占比（%）→ {date: pct}，L1 入场过滤用。

    **口径披露**：旧脚本读全市场 `limit_up_ratio_daily_2008_2026.csv`（已不留存），
    此处改为**用抽样池即时估计**——因感受训练的 codes 是全 A 股的**随机抽样**，
    样本比例是全市场比例的无偏估计。代价是 n≈500 有小样本噪声。

    **该近似不影响本工具的结论**：L1 只决定"哪些交易日可入场"，对同一
    `selection` 下的所有止损档是**同一份入场样本**，故**止损效应归因对
    L1 口径不敏感**——受影响的只是收益绝对水平。
    （若需精确口径，改用 `btf.data.liq.limit_stats` 的 `stk_limit` 同源做法
    批量统计 `up_limit`，但需承担全区间 IO。）
    """
    frames = []
    for code, df in prices.items():
        if df.empty:
            continue
        thr = _limit_up_thr(code)
        d = df[["trade_date", "pct_chg"]].copy()
        d["is_lu"] = d["pct_chg"] >= thr
        frames.append(d)
    if not frames:
        return {}
    all_df = pd.concat(frames, ignore_index=True)
    g = all_df.groupby("trade_date").agg(n=("is_lu", "size"), k=("is_lu", "sum"))
    out: dict[str, float] = {}
    for d, n, k in zip(g.index, g["n"], g["k"]):
        if n > 0:
            out[str(d)] = float(k) / float(n) * 100.0
    print(f"    涨停占比 {len(out)} 日（抽样池估计，n≈{len(prices)}）")
    return out


def build_ma20_scale(store: YearTableStore, start: str, end: str,
                     symbol: str = "000300.SH") -> dict[str, float]:
    """HS300 MA20 仓位缩放：收盘 > MA20 → 1.0，否则 0.5（前 19 日补 1.0）。"""
    t = store.read_range("index_daily", start, end,
                         columns=["ts_code", "trade_date", "close"])
    df = t.to_pandas()
    df = df[df["ts_code"].astype(str) == symbol].copy()
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.sort_values("trade_date").reset_index(drop=True)
    ma = df["close"].rolling(window=MA20_WINDOW).mean()
    above = df["close"] > ma
    scale = np.where(above, 1.0, 0.5)
    out = dict(zip(df["trade_date"], scale))
    for d in df["trade_date"].values[:MA20_WINDOW - 1]:
        out[str(d)] = 1.0
    return out


def build_crisis_days(start: str, end: str) -> tuple[set[str], dict]:
    """CRISIS/RECOVERY 交易日集合 + 完整 LIQ 序列。

    用 **btf 真源** `precompute_liq_series`（按年装载 + Arrow 向量化，十年 ~4s），
    取代逐日 `state_of`（同区间约 760s，且会重复 IO）。语义由
    `tests/perf/test_liq_precompute.py` 钉住与逐日 `state_of` 等价。
    """
    from btf.data.liq import precompute_liq_series

    rules = json.loads(MIRROR.read_text(encoding="utf-8"))["rules"]
    t0 = time.perf_counter()
    series = precompute_liq_series(start, end, rules.get("L0_LIQ"))
    bad = {d for d, st in series.items()
           if st.status in ("CRISIS", "RECOVERY")}
    print(f"    LIQ 序列 {len(series)} 日 / CRISIS+RECOVERY {len(bad)} 日 "
          f"({time.perf_counter()-t0:.1f}s)")
    return bad, series


def load_fundamentals(store: YearTableStore, codes: list[str], start: str,
                      end: str) -> tuple[dict[str, pd.DataFrame],
                                         dict[str, pd.DataFrame]]:
    """估值与年报 ROE → ({code: daily_basic_df}, {code: fina_df})。"""
    t0 = time.perf_counter()
    db = store.read_codes("daily_basic", codes, start, end,
                          columns=["ts_code", "trade_date", "pe_ttm", "pb",
                                   "dv_ttm"]).to_pandas()
    db["trade_date"] = db["trade_date"].astype(str)
    basics = {str(c): g.sort_values("trade_date").reset_index(drop=True)
              for c, g in db.groupby("ts_code", sort=True)}

    yr0 = int(start[:4]) - 3
    fi = store.read_codes("fina_indicator", codes, f"{yr0}0101", end,
                          columns=["ts_code", "ann_date", "end_date",
                                   "roe_waa"]).to_pandas()
    finas: dict[str, pd.DataFrame] = {}
    if not fi.empty:
        fi["ann_date"] = fi["ann_date"].astype(str)
        fi["end_date"] = fi["end_date"].astype(str)
        for c, g in fi.groupby("ts_code", sort=True):
            g = g[(g["ann_date"] <= end) & (g["end_date"].str.endswith("1231"))]
            finas[str(c)] = g.sort_values("ann_date").reset_index(drop=True)
    print(f"    基本面 {len(basics)}/{len(finas)} 只 / "
          f"{time.perf_counter()-t0:.1f}s")
    return basics, finas


def check_selection(code: str, date: str, scheme: str,
                    basics: dict, finas: dict) -> bool | None:
    """是否入选 → True/False/None（None = 数据不足）。

    `BASE` 无过滤（旧脚本行为）；`current` = 现行四条硬筛；`pr` = PR<1.5。
    """
    if scheme == "BASE":
        return True
    db = basics.get(code)
    if db is None:
        return None
    hit = db[db["trade_date"] == date]
    if hit.empty:
        return None
    row = hit.iloc[0]
    pe = row["pe_ttm"]
    pb = row["pb"]
    dv = row["dv_ttm"]
    roe = _pit_roe(finas.get(code), date)
    pe_ok = pd.notna(pe)
    pb_ok = pd.notna(pb)
    dv_ok = pd.notna(dv)
    if scheme == "current":
        if not (pe_ok and pb_ok and dv_ok) or roe is None:
            return None
        return bool(pe > 0 and pe < 30.0 and pb < 3.0
                    and roe > 5.0 and dv > 1.0)
    if scheme == "pr":
        if not pe_ok or roe is None or roe <= 0:
            return None
        return bool(pe > 0 and (pe / roe) < 1.5)
    return None


def _pit_roe(fina: pd.DataFrame | None, date: str) -> float | None:
    """PIT 年报 ROE：ann_date ≤ date 的最新年报 roe_waa。"""
    if fina is None or fina.empty:
        return None
    g = fina[fina["ann_date"] <= date]
    if g.empty:
        return None
    g = g.sort_values(["end_date", "ann_date"])
    return float(g.iloc[-1]["roe_waa"])


def simulate(buy_price: float, path: list[tuple[str, float, float]],
             stop_threshold: float | None, code: str,
             position_scale: float) -> dict | None:
    """单笔持有模拟（忠实复刻 l2 脚本 `simulate_trade` 净效果）。

    path: [(trade_date, adj_close, pct_chg), ...] 从入场日**次日起**。
    """
    if buy_price <= 0 or not path:
        return None
    remaining, tp1_done = 1.0, False
    proceeds, running_max = 0.0, buy_price
    exit_reason, hold_days = None, 0

    for i, (td, adj_close, pct_chg) in enumerate(path):
        hold_days += 1
        running_max = max(running_max, adj_close)
        ratio = adj_close / buy_price

        if stop_threshold is not None and adj_close <= buy_price * stop_threshold:
            thr_dn = -19.8 if _is_chiyeban(code) else -9.8
            is_ld = pct_chg is not None and pd.notna(pct_chg) and pct_chg <= thr_dn
            if is_ld and hold_days < MAX_HOLD_DAYS:
                sold_range = path[i + 1:i + 1 + LIMIT_DOWN_MAX_RETRY]
                if sold_range:
                    nxt = sold_range[0]
                    proceeds += remaining * (nxt[1] / buy_price)
                    hold_days += 1
                    exit_reason = "stop_loss_ld"
                    break
                proceeds += remaining * ratio
                exit_reason = "stop_loss_ld_f"
                break
            proceeds += remaining * ratio
            exit_reason = "stop_loss"
            break

        if not tp1_done and ratio >= TP1_TRIGGER:
            af = min(TP1_SELL_FRAC, remaining)
            proceeds += af * ratio
            remaining -= af
            tp1_done = True

        if tp1_done and remaining > 0 and adj_close <= running_max * TRAILING_STOP:
            proceeds += remaining * ratio
            exit_reason = "trailing_stop"
            break

        if hold_days >= MAX_HOLD_DAYS:
            proceeds += remaining * ratio
            exit_reason = "time_stop"
            break

    if exit_reason is None:
        proceeds += remaining * (path[-1][1] / buy_price)
        exit_reason = "data_end"

    ret = (proceeds - 1.0) - 2 * COST_SINGLE
    return {"total_return": ret * position_scale,
            "exit_reason": exit_reason, "hold_days": hold_days}


def calc_metrics(trades: list[dict], label: str) -> dict:
    """与旧脚本 `calc_metrics` 同口径：单笔均值 / 胜率 / 年化夏普 / 持有天数。"""
    n = len(trades)
    if n == 0:
        return {"label": label, "n": 0, "mean": None, "win_rate": None,
                "sharpe": None, "avg_hold": None, "reasons": {}}
    rets = np.array([t["total_return"] for t in trades], dtype=float)
    holds = np.array([t["hold_days"] for t in trades], dtype=float)
    mean_r = float(np.mean(rets))
    avg_hold = float(np.mean(holds))
    win = float(np.mean(rets > 0))
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    sharpe = (mean_r / sd * math.sqrt(252.0 / max(avg_hold, 1.0))
              if sd > 0 else 0.0)
    reasons: dict[str, int] = defaultdict(int)
    for t in trades:
        reasons[t["exit_reason"]] += 1
    return {"label": label, "n": n, "mean": mean_r, "win_rate": win,
            "sharpe": sharpe, "avg_hold": avg_hold, "reasons": dict(reasons)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="止损/止盈敏感性消融")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--start", default="20160101")
    ap.add_argument("--end", default="20260923")
    ap.add_argument("--n-stocks", type=int, default=N_STOCKS)
    ap.add_argument("--ref-year", type=int, default=2024,
                    help="构建 A 股全集用的参考年份")
    ap.add_argument("--quick", action="store_true", help="小样本冒烟（50 只）")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not MIRROR.is_file():
        print(f"[error] 规则镜像缺失：{MIRROR}", file=sys.stderr)
        return 1

    t_start = time.perf_counter()
    store = YearTableStore(Path(MARKET_DATA_DIR))
    start, end = args.start, args.end
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.quick:
        args.n_stocks = 50

    print("=" * 78)
    print("止损 / 止盈敏感性消融（决策项 2：分离参数效应与选股效应）")
    print(f"期间 {start} ~ {end} | 抽样 {args.n_stocks} 只 | seed {SEED}")
    print("=" * 78)

    print("\n[1/6] 构建 universe 与抽样")
    universe = build_universe(store, args.ref_year)
    codes = sample_codes(universe, args.n_stocks)
    print(f"    A股全集 {len(universe)} → 抽样 {len(codes)}")

    print("\n[2/6] 加载后复权价格")
    prices = load_prices(store, codes, start, end)

    print("\n[3/6] 重算入场过滤条件（涨停占比 / CRISIS / HS300 MA20）")
    lu = build_limit_up_ratio(prices)
    crisis, _liq_series = build_crisis_days(start, end)
    ma_scale = build_ma20_scale(store, start, end)
    non_crisis = sorted(d for d in ma_scale if d not in crisis)

    print("\n[4/6] 加载基本面（PIT）")
    basics, finas = load_fundamentals(store, codes, start, end)

    print("\n[5/6] 生成入场样本 + 逐组合模拟")
    entries: list[tuple[str, str, float, float]] = []   # (code, date, buy, scale)
    for si, code in enumerate(codes):
        df = prices.get(code)
        if df is None or len(df) < 5:
            continue
        stock_dates = set(df["trade_date"])
        available = [d for d in non_crisis if d in stock_dates]
        if len(available) < 5:
            continue
        rng = np.random.RandomState(SEED * 1000 + si)
        n = min(N_ENTRIES_PER_STOCK, len(available))
        idx = rng.choice(len(available), size=n, replace=False)
        pos = {d: i for i, d in enumerate(df["trade_date"])}
        for k in sorted(idx):
            d = available[k]
            if lu.get(d, 0.0) <= L1_GREEN_THRESHOLD:
                continue                                  # L1 入场过滤
            row = df.iloc[pos[d]]
            buy = float(row["adj_close"])
            if buy <= 0 or not math.isfinite(buy):
                continue
            entries.append((code, d, buy, ma_scale.get(d, 1.0), pos[d]))
    n_stock_used = len({e[0] for e in entries})
    print(f"    入场样本 {len(entries)} 笔 / 涉及 {n_stock_used} 只"
          f"（L1 过滤后）")

    #: 每个 (selection, 止损档) → trades
    grid: dict[tuple[str, str], list[dict]] = {
        (s, sl): [] for s in SELECTIONS for sl, _v in STOP_LOSS_LEVELS}
    paths_cache: dict[str, list[tuple[str, float, float]]] = {}
    for code, d, buy, scale, p0 in entries:
        df = prices[code]
        sub = df.iloc[p0 + 1:]
        key = f"{code}|{d}"
        path = paths_cache.get(key)
        if path is None:
            path = list(zip(sub["trade_date"], sub["adj_close"],
                            sub["pct_chg"]))
            paths_cache[key] = path
        if not path:
            continue
        verdicts = {s: check_selection(code, d, s, basics, finas)
                    for s in SELECTIONS}
        for sl_name, thr in STOP_LOSS_LEVELS:
            for s in SELECTIONS:
                if verdicts[s] is not True:
                    continue
                t = simulate(buy, path, thr, code, scale)
                if t:
                    grid[(s, sl_name)].append(t)

    print("\n[6/6] 指标汇总")
    results: dict[str, dict] = {}
    for s in SELECTIONS:
        for sl_name, _thr in STOP_LOSS_LEVELS:
            key = f"{s}|{sl_name}"
            results[key] = calc_metrics(grid[(s, sl_name)], key)
            r = results[key]
            print(f"  {key:22s} n={r['n']:5d} | 每笔均值 "
                  f"{(r['mean'] or 0)*100:6.2f}% | 胜率 "
                  f"{(r['win_rate'] or 0):5.1%} | 夏普 {r['sharpe'] or 0:5.2f}"
                  f" | 持有 {r['avg_hold'] or 0:5.1f}d")

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    payload = {
        "generated_at": stamp, "engine": "btf",
        "tool": "run_stoploss_ablation",
        "purpose": "分离止损参数效应与选股效应（2026-10-01 令 · 决策项 2）",
        "period": {"start": start, "end": end},
        "params": {"n_stocks": args.n_stocks, "n_entries_per_stock":
                   N_ENTRIES_PER_STOCK, "seed": SEED,
                   "max_hold_days": MAX_HOLD_DAYS,
                   "tp1": [TP1_TRIGGER, TP1_SELL_FRAC],
                   "trailing_stop": TRAILING_STOP,
                   "cost_single": COST_SINGLE,
                   "l1_green_threshold": L1_GREEN_THRESHOLD},
        "n_entries": len(entries), "n_stocks_used": n_stock_used,
        "stop_loss_levels": [k for k, _v in STOP_LOSS_LEVELS],
        "selections": list(SELECTIONS),
        "results": results,
        "wall_s": round(time.perf_counter() - t_start, 1),
    }
    jpath = out_dir / f"stoploss-ablation-{stamp}.json"
    jpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print(f"\n[out] {jpath}  ({payload['wall_s']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
