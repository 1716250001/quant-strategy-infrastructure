#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/leverage_monitor.py — 系统性风险维度·杠杆监控（D2 拆分产物）
====================================================================
来源：2026-09-25 D2 从 fix_and_resonance.py（892 行混合体）拆出。
**只保留风险维度**：B 口径杠杆热度 + 黄/红警戒分级。
β 合成 / 仓位映射 / 共振 / 四象限等旧逻辑已随原文件归档（_archive/日报链路-20260924/）。

口径（与 fetch/leverage.py 2026-09-22 修复版完全一致，勿漂移）：
  B口径 b_pct = (rzmre + rzche) 三所求和 / (沪000001.SH + 深399001.SZ 成交额) × 100
  · margin 为 T-1 延迟数据；分母必须用**与两融同期**的交易日（跨日配比会失真）
  · index_daily.amount 单位千元（×1000 转元）；margin 三列单位元
  · 曾有单交易所 bug（b_ratio 低估 2.1 倍导致黄/红警长期漏报）——按交易所求和是铁律

阈值（锚 2015 牛市峰值 21.5%）：
  ≥20% 红警（杠杆过热熔断）｜≥18% 黄警（偏高）｜<18% 正常

用法：
  python -m tools.leverage_monitor              # 最新（自动找 margin 最新有数日）
  python -m tools.leverage_monitor 20260924     # 指定基准日（取其前一交易日两融）
输出：JSON 一行式（供作战包直接引用）+ 人读摘要。

⚠ 纪律（D2 红线）：本模块只产出**风险维度与证据**，不产出 β 值、仓位建议、
  宽基配仓——防 v7.5 已废弃的择时逻辑从后门回归。
"""
import json
import sys

import pandas as pd

# F2 修复（2026-09-25 架构审查 v2）：路径接回 config 单一真源
# （原为硬编码 r"D:\全量数据\market_data"，违反自家纪律——库迁移时会静默断链）
from config import MARKET_DATA_DIR

# 阈值常量（单一真源；与旧 build_dual_warning 一致）
YELLOW_THRESHOLD = 18.0   # B口径 ≥18% 黄警
RED_THRESHOLD = 20.0      # B口径 ≥20% 红警
RED_ANCHOR = "2015牛市峰值21.5%"


def _read_parquet(table: str, trade_date: str) -> pd.DataFrame:
    year = trade_date[:4]
    return pd.read_parquet(rf"{MARKET_DATA_DIR}\{table}\{year}.parquet")


def _latest_margin_date(trade_date: str = None, max_back: int = 10) -> str:
    """找 margin 表内 ≤ trade_date 的最新有数日期（两融 T-1）。

    F2 修复（2026-09-25）：原实现循环内每个候选日都重读同一年份 parquet
    （最多 11 次整读）；改为按年份缓存——每年文件只读一次，内存内回退匹配。
    """
    if trade_date is None:
        # 从 index_daily 找最新交易日（大盘必有）
        import os
        years = sorted(os.listdir(rf"{MARKET_DATA_DIR}\index_daily"))
        df = pd.read_parquet(rf"{MARKET_DATA_DIR}\index_daily\{years[-1]}")
        trade_date = df[df["ts_code"] == "000001.SH"]["trade_date"].max()
    base = pd.Timestamp(trade_date)
    year_cache = {}          # 年份 → 该年 margin 表 DataFrame（每年只读一次）
    for back in range(max_back + 1):
        d = (base - pd.Timedelta(days=back)).strftime("%Y%m%d")
        y = d[:4]
        try:
            if y not in year_cache:
                year_cache[y] = pd.read_parquet(rf"{MARKET_DATA_DIR}\margin\{y}.parquet")
            mdf = year_cache[y]
            if not mdf[mdf["trade_date"] == d].empty:
                return d
        except (FileNotFoundError, OSError):
            continue
    raise RuntimeError(f"margin 表在 {trade_date} 前 {max_back} 天内无数据")


def get_leverage_state(trade_date: str = None) -> dict:
    """从本地库计算 B 口径杠杆热度与警戒分级。

    trade_date=None → 以 index_daily 最新交易日为基准（作战包日常用法）。
    返回 dict：margin_date / b_pct / rzye_trillion / level / label / action / ref。
    """
    mdate = _latest_margin_date(trade_date)
    marg = _read_parquet("margin", mdate)
    marg = marg[marg["trade_date"] == mdate]
    if marg.empty:
        raise RuntimeError(f"margin {mdate} 空数据")
    # 铁律：按交易所求和（曾因单交易所口径低估 2.1 倍漏报红警）
    rzye = float(marg["rzye"].sum())
    rzmre = float(marg["rzmre"].sum())
    rzche = float(marg["rzche"].sum())

    idx = _read_parquet("index_daily", mdate)
    amt = idx[(idx["trade_date"] == mdate) & (idx["ts_code"].isin(["000001.SH", "399001.SZ"]))]
    if amt.empty:
        raise RuntimeError(f"index_daily {mdate} 无沪/深指数成交额（两融分母缺失）")
    total_turnover = float(amt["amount"].astype(float).sum()) * 1000  # 千元→元

    b_pct = (rzmre + rzche) / total_turnover * 100 if total_turnover > 0 else 0.0

    if b_pct >= RED_THRESHOLD:
        level, label = "red", "杠杆过热熔断"
        action = "三级强制空仓，二级上限15%"
    elif b_pct >= YELLOW_THRESHOLD:
        level, label = "yellow", "杠杆占比偏高"
        action = "关注杠杆热度，三级仓位减半"
    else:
        level, label, action = "normal", "正常", ""

    return {
        "margin_date": mdate,                      # 两融数据日（T-1 口径）
        "b_pct": round(b_pct, 2),                  # B口径（买入+偿还 / 沪深成交额）
        "margin_balance_trillion": round(rzye / 1e12, 4),
        "level": level,
        "label": label,
        "action": action,
        "ref": RED_ANCHOR if level != "normal" else "",
        "thresholds": f"黄警≥{YELLOW_THRESHOLD:.0f}% / 红警≥{RED_THRESHOLD:.0f}%",
        "note": "两融为T-1延迟；三所求和；分母与两融同期（沪+深成交额）",
    }


def build_dual_warning(supp: dict) -> dict:
    """[兼容保留] 原 fix_and_resonance.build_dual_warning 逐行照搬——
    供旧 supplement JSON（含 leverage_heat.b_ratio）复算对照用。新代码请用 get_leverage_state()。"""
    dual_warning = {"leverage": None, "combined_level": "normal", "actions": []}
    b_ratio = supp.get('leverage_heat', {}).get('b_ratio', 0)
    if b_ratio > 0:
        b_pct = b_ratio * 100
        if b_pct >= RED_THRESHOLD:
            dual_warning['leverage'] = {"level": "red", "value": b_pct,
                "label": "杠杆过热熔断", "ref": RED_ANCHOR,
                "action": "三级强制空仓，二级上限15%"}
        elif b_pct >= YELLOW_THRESHOLD:
            dual_warning['leverage'] = {"level": "yellow", "value": b_pct,
                "label": "杠杆占比偏高", "ref": f"B口径≥{YELLOW_THRESHOLD:.0f}%",
                "action": "关注杠杆热度，三级仓位减半"}
        else:
            dual_warning['leverage'] = {"level": "normal", "value": b_pct,
                "label": "正常", "ref": ""}
        print(f"  [警戒-杠杆] B口径={b_pct:.1f}% → {dual_warning['leverage']['level']}")
    levels = []
    if dual_warning['leverage']:
        levels.append(dual_warning['leverage']['level'])
    if 'red' in levels:
        dual_warning['combined_level'] = 'red'
        dual_warning['actions'] = [a['action'] for a in [dual_warning.get('leverage')]
                                   if a and a.get('level') == 'red']
    elif 'yellow' in levels:
        dual_warning['combined_level'] = 'yellow'
    print(f"  [警戒-综合] = {dual_warning['combined_level']}")
    return dual_warning


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    trade_date = argv[0] if argv else None
    st = get_leverage_state(trade_date)
    print(json.dumps(st, ensure_ascii=False))
    print(f"\n[人读] 两融@{st['margin_date']}（T-1）B口径={st['b_pct']}% → "
          f"{st['level'].upper()} {st['label']}（{st['thresholds']}，锚{RED_ANCHOR}）"
          + (f" | 动作: {st['action']}" if st['action'] else ""))


if __name__ == "__main__":
    main()
