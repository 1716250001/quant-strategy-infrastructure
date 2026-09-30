#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/etf_flow.py — 宽基 ETF 份额与申赎资金流（D2 拆分产物）
================================================================
来源：2026-09-25 D2 从 fix_and_resonance.py 拆出。
**只保留份额/资金流证据层**：7 只宽基 ETF 份额变化 + 5/20 日申赎金额估算。
β 合成中的 fund_flow 因子导出（derive_fund_flow）一并保留（纯函数，不接决策）。

数据源（勿改）：
  · 上交所份额：akshare fund_etf_scale_sse —— **T+1 发布**（沪市 ETF 为上一交易日口径）
  · 深交所份额：tushare fund_share —— 当日口径
  · 净值：tushare fund_daily（失败时用 FALLBACK_NAVS 兜底）

用法：
  python -m tools.etf_flow                # 最新交易日（读 report_data 找基准）
  python -m tools.etf_flow 20260924       # 指定交易日
输出：份额变化与申赎摘要（供作战包候选清单作资金面证据引用）。

⚠ 纪律（D2 红线）：本模块只产出**资金面证据**，不产出 β 值、仓位建议。
⚠ 口径提醒：引用沪市 ETF 份额变化时必须标注 T+1（上交所口径）。
"""
import argparse
import time

import akshare as ak
import numpy as np

from common.calendar import recent_trade_dates
from fetch.base import get_pro

# ── 常量（自 fix_and_resonance.py 原样搬移，勿改口径）─────────────────
INDEX_ETF_MAP = {
    "上证50ETF":    {"codes": ["510050"], "market": "sse"},
    "沪深300ETF":   {"codes": ["510300"], "market": "sse"},
    "中证500ETF":   {"codes": ["510500"], "market": "sse"},
    "中证1000ETF":  {"codes": ["512100"], "market": "sse"},
    "中证2000ETF":  {"codes": ["563300"], "market": "sse"},
    "科创50ETF":    {"codes": ["588000"], "market": "sse"},
    "创业板ETF":    {"codes": ["159915"], "market": "szse"},
}
REP_ETF_MAP = {name: info["codes"][0] for name, info in INDEX_ETF_MAP.items()}

FALLBACK_NAVS = {
    "上证50ETF": 3.05, "沪深300ETF": 4.80, "中证500ETF": 8.60,
    "中证1000ETF": 3.40, "中证2000ETF": 1.50,
    "科创50ETF": 1.00, "创业板ETF": 3.90,
}

ETF_TO_INDEX = {
    "上证50ETF": "上证50", "沪深300ETF": "沪深300", "中证500ETF": "中证500",
    "中证1000ETF": "中证1000", "中证2000ETF": "中证2000",
    "创业板ETF": "创业板指", "科创50ETF": "科创50",
}


# ── 份额抓取（原 fetch_etf_shares 逐行照搬）─────────────────────────
def fetch_etf_shares(trade_dates, code_to_index, tdate):
    """抓取各宽基指数对应 ETF 的份额序列。

    - 上交所: akshare fund_etf_scale_sse（T+1 发布，当日未出则跳过）
    - 深交所: tushare fund_share（当日口径）

    返回: (aggregate_by_date: {指数名: {日期: 份额}}, sse_latest_ok_date)
    """
    aggregate_by_date = {name: {} for name in INDEX_ETF_MAP}
    sse_latest_ok_date = None

    for td in trade_dates:
        try:
            df = ak.fund_etf_scale_sse(date=td)
            if df is None or len(df) == 0:
                print(f"  [INFO] SSE {td}: 0 条（上交所份额T+1发布，当日未出，跳过）")
                time.sleep(0.3)
                continue
            td_clean = str(td)
            for _, row in df.iterrows():
                code = str(row['基金代码'])
                idx_name = code_to_index.get(code)
                if idx_name:
                    shares = float(row['基金份额'])
                    aggregate_by_date[idx_name][td_clean] = (
                        aggregate_by_date[idx_name].get(td_clean, 0.0) + shares)
            if sse_latest_ok_date is None:
                sse_latest_ok_date = str(td)
            print(f"  SSE {td}: {len(df)} funds")
        except KeyError as e:
            print(f"  [INFO] SSE {td}: 当日份额未发布(T+1节奏)，跳过 | {str(e)[:60]}")
        except Exception as e:
            print(f"  [WARN] SSE {td}: {str(e)[:60]}")
        time.sleep(0.3)

    # 深交所份额（通过 tushare fund_share 获取 159915）
    try:
        pro = get_pro()
        t_start = min(trade_dates) if trade_dates else tdate
        t_end = max(trade_dates) if trade_dates else tdate
        df_fund = pro.fund_share(ts_code='159915.SZ', start_date=t_start, end_date=t_end)
        if df_fund is not None and len(df_fund) > 0:
            df_fund['trade_date'] = df_fund['trade_date'].astype(str)
            df_fund['shares_raw'] = df_fund['fd_share'].astype(float) * 10000
            for _, row in df_fund.iterrows():
                td = str(row['trade_date'])
                idx_name = code_to_index.get('159915')
                if idx_name:
                    aggregate_by_date[idx_name][td] = (
                        aggregate_by_date[idx_name].get(td, 0.0) + float(row['shares_raw']))
            print(f"  SZSE fund_share: {len(df_fund)} records for 159915")
    except Exception as e:
        print(f"  [WARN] SZSE fund_share failed: {e}")

    return aggregate_by_date, sse_latest_ok_date


# ── 份额变化输出（原 build_shares_output 逐行照搬）──────────────────
def build_shares_output(tformat, tdate, trade_dates, aggregate_by_date, sse_asof):
    """生成 shares_output（含各指数份额变化），返回 (shares_output, all_shares)"""
    all_shares = {}
    for idx_name, by_date in aggregate_by_date.items():
        records = [{"date": d, "shares": s, "source": "sse+szse"}
                   for d, s in sorted(by_date.items())]
        all_shares[idx_name] = records

    shares_output = {
        "date": tformat,
        "trade_dates": trade_dates,
        "etf_shares": {},
        "data_asof": {
            "sse": sse_asof,
            "szse": tdate,
            "note": "上交所份额T+1发布(沪市ETF为上一交易日口径)；深交所(tushare)为当日口径",
        },
    }

    for name in INDEX_ETF_MAP:
        records = all_shares.get(name, [])
        if len(records) >= 2:
            latest_total = records[-1]['shares']
            prev5_total = records[max(0, len(records) - 6)]['shares']
            prev20_total = records[0]['shares']
            chg_5d = round((latest_total / prev5_total - 1) * 100, 2)
            chg_20d = round((latest_total / prev20_total - 1) * 100, 2)
            shares_output["etf_shares"][name] = {
                "code": INDEX_ETF_MAP[name]["codes"],
                "latest_shares": latest_total,
                "chg_5d_pct": chg_5d,
                "chg_20d_pct": chg_20d,
                "history": records,
                "data_points": len(records),
                "source": "sse+szse",
                "n_etfs": len(INDEX_ETF_MAP[name]["codes"]),
            }
            units = latest_total / 1e8
            print(f"  {name:12s}: {units:.1f}亿 | 5日={chg_5d:+.2f}% | 20日={chg_20d:+.2f}%")
        else:
            print(f"  {name:12s}: 无数据")

    return shares_output, all_shares


# ── 申赎金额估算（原 compute_fund_flows 逐行照搬）───────────────────
def compute_fund_flows(all_shares, trade_dates):
    """由份额序列 + 代表性ETF净值估算 5/20 日申赎金额。

    返回 (fund_flows_summary, all_flow_5d, all_flow_20d)
    """
    pro_fund = get_pro()
    etf_prices = {}
    for name, code in REP_ETF_MAP.items():
        try:
            ts_code = f"{code}.SZ" if code.startswith('159') else f"{code}.SH"
            df = pro_fund.fund_daily(ts_code=ts_code,
                                     start_date=trade_dates[-1], end_date=trade_dates[0],
                                     fields='trade_date,close')
            if df is not None and len(df) > 0:
                etf_prices[name] = {row['trade_date']: float(row['close']) for _, row in df.iterrows()}
        except Exception as e:
            print(f"  [WARN] {name}({code}) 价格获取失败: {e}")

    fund_flows_summary = {}
    all_flow_5d = []
    all_flow_20d = []

    for name in INDEX_ETF_MAP:
        records = all_shares.get(name, [])
        prices = etf_prices.get(name, {})
        if len(records) < 3:
            fund_flows_summary[name] = {"flow_5d": None, "flow_20d": None, "nav": None, "nav_unit": "—"}
            continue

        latest_nav = None
        for r in reversed(records):
            if r['date'] in prices:
                latest_nav = prices[r['date']]
                break
        if latest_nav is None:
            latest_nav = FALLBACK_NAVS.get(name, 1.0)

        # 过滤份额骤降（可能是拆分/口径变化）
        n_avail = len(records)
        complete_mask = [True] * n_avail
        for i in range(1, n_avail):
            if records[i]['shares'] < records[i - 1]['shares'] * 0.6:
                complete_mask[i] = False
        complete_records = [(records[i], records[i]['date']) for i in range(n_avail) if complete_mask[i]]

        if len(complete_records) < 3:
            fund_flows_summary[name] = {
                "flow_5d": None, "flow_20d": None,
                "nav": round(latest_nav, 3), "nav_unit": "元/份",
            }
            continue

        flow_5d = 0.0
        flow_20d = 0.0
        n_comp = len(complete_records)
        for i in range(1, n_comp):
            prev_rec, _ = complete_records[i - 1]
            curr_rec, td = complete_records[i]
            price_today = prices.get(td, latest_nav)
            daily_flow = (curr_rec['shares'] - prev_rec['shares']) * price_today
            idx_from_end = n_comp - 1 - i
            if idx_from_end < 5:
                flow_5d += daily_flow
            if idx_from_end < 20:
                flow_20d += daily_flow

        flow_5d_yi = flow_5d / 1e8
        flow_20d_yi = flow_20d / 1e8
        fund_flows_summary[name] = {
            "flow_5d": round(flow_5d_yi, 2),
            "flow_20d": round(flow_20d_yi, 2),
            "nav": round(latest_nav, 3),
            "nav_unit": "元/份",
        }
        if abs(flow_5d_yi) > 0.001:
            all_flow_5d.append(flow_5d_yi)
        if abs(flow_20d_yi) > 0.001:
            all_flow_20d.append(flow_20d_yi)

    return fund_flows_summary, all_flow_5d, all_flow_20d


def derive_fund_flow(all_flow_5d, all_flow_20d):
    """由平均申赎金额导出 fund_flow 因子（±4 量级），返回 (short, long)。
    [仅证据层保留]——不接决策（D2 红线）。"""
    avg_flow5 = np.mean(all_flow_5d) if all_flow_5d else 0
    avg_flow20 = np.mean(all_flow_20d) if all_flow_20d else 0
    short = round(np.clip(avg_flow5 / 100, -4, 4), 1)
    long_ = round(np.clip(avg_flow20 / 300, -4, 4), 1)
    return short, long_


def main(argv=None):
    argv = list(argv or [])
    tdate = argv[0] if argv else None
    if tdate is None:
        from common.calendar import recent_trade_dates as _rtd
        tdate = _rtd(1)[0]
    trade_dates = recent_trade_dates(20, end_date=tdate)
    code_to_index = {}
    for name, info in INDEX_ETF_MAP.items():
        for c in info["codes"]:
            code_to_index[c] = name

    print(f"[etf_flow] 基准日 {tdate}（近20交易日窗口）")
    agg, sse_ok = fetch_etf_shares(reversed(trade_dates), code_to_index, tdate)
    tformat = f"{tdate[:4]}-{tdate[4:6]}-{tdate[6:]}"
    shares_output, all_shares = build_shares_output(tformat, tdate, trade_dates, agg, sse_ok)
    flows, _, _ = compute_fund_flows(all_shares, trade_dates)

    print("\n[申赎摘要] (亿元；沪市份额为T+1口径)")
    for name in INDEX_ETF_MAP:
        f = flows.get(name, {})
        print(f"  {name:12s}: 5日={f.get('flow_5d')} 20日={f.get('flow_20d')}")
    asof = shares_output["data_asof"]
    print(f"\n[data_asof] sse={asof['sse']} szse={asof['szse']}｜{asof['note']}")


if __name__ == "__main__":
    main()
