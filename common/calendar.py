# -*- coding: utf-8 -*-
"""
common/calendar.py — 交易日历统一读取
======================================
trade_cal.parquet 的唯一读取入口，替换此前 5 处重复实现
（leverage / gap_update / fund_nav_update / redtide_supply / full_download）。

统一约定：
  - exchange 默认 "SSE"
  - 返回的日期均为 YYYYMMDD 字符串
  - 所有区间函数默认升序；prev_trade_dates 返回倒序（最近在前）
"""
import os
from datetime import datetime

import pandas as pd

from common.paths import META_DIR

CAL_PATH = os.path.join(META_DIR, "trade_cal.parquet")


def load_trade_cal(exchange="SSE", only_open=True):
    """读取本地交易日历，返回 DataFrame（cal_date 已转字符串）。"""
    if not os.path.exists(CAL_PATH):
        raise FileNotFoundError(f"交易日历不存在: {CAL_PATH}（请先运行全量下载）")
    cal = pd.read_parquet(CAL_PATH)
    if exchange and "exchange" in cal.columns:
        cal = cal[cal["exchange"] == exchange]
    if only_open and "is_open" in cal.columns:
        cal = cal[cal["is_open"] == 1]
    cal = cal.copy()
    if "cal_date" in cal.columns:
        cal["cal_date"] = cal["cal_date"].astype(str)
    return cal


def open_dates(start_date=None, end_date=None, exchange="SSE"):
    """区间内交易日列表（升序，闭区间）。

    open_dates("20260801", "20260831") -> ['20260803', ...]
    """
    cal = load_trade_cal(exchange)
    dates = cal["cal_date"].sort_values()
    if start_date:
        dates = dates[dates >= str(start_date)]
    if end_date:
        dates = dates[dates <= str(end_date)]
    return dates.tolist()


def recent_trade_dates(n, end_date=None, exchange="SSE"):
    """最近 n 个交易日（升序）。

    end_date 默认今天——trade_cal 含未来交易日，必须以今天为上限。
    """
    end = end_date or datetime.now().strftime("%Y%m%d")
    dates = open_dates(end_date=end, exchange=exchange)
    return dates[-n:] if n and n > 0 else []


def prev_trade_dates(trade_date, n=2, exchange="SSE"):
    """trade_date 之前的 n 个交易日（倒序，最近在前）。"""
    dates = open_dates(end_date=str(trade_date), exchange=exchange)
    dates = [d for d in dates if d < str(trade_date)]
    return dates[-n:][::-1] if n and n > 0 else []


def trade_dates_between(start_date, end_date, exchange="SSE"):
    """(start_date, end_date] 区间交易日（升序，左开右闭）。"""
    dates = open_dates(start_date=str(start_date), end_date=str(end_date), exchange=exchange)
    return [d for d in dates if d > str(start_date)]


# ============================================================
# 美股交易日历（2026-09-19 新增）
# ============================================================
# 为什么单独一份:
#   metadata/trade_cal.parquet 只含 A 股各交易所（SSE/SZSE/BSE...），
#   美股日历来自独立接口 us_tradecal，落在**库目录**（market_data/us_tradecal），
#   不在 metadata 下，也**不能**混进 A 股日历（交易日差异明显）。
#   实测 2026-09：A 股 21 个交易日 vs 美股 13 个（A 股有国庆前调休等）。
#
# ⚠ 这是 by_date 类"按日拉全市场"用美股日历的前提（如 us_daily）。
_US_CAL_REL = os.path.join("us_tradecal", "us_tradecal.parquet")


def _us_cal_path():
    """美股日历文件路径（在库目录下，非 metadata）。"""
    try:
        from config import MARKET_DATA_DIR
    except Exception:
        MARKET_DATA_DIR = os.path.dirname(os.path.dirname(META_DIR))
    return os.path.join(MARKET_DATA_DIR, _US_CAL_REL)


def load_us_cal(only_open=True):
    """读取本地美股交易日历（us_tradecal）。文件缺失时抛错，避免静默返空。"""
    p = _us_cal_path()
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"美股交易日历不存在: {p}"
            f"（请先运行 python main.py backfill --only us_tradecal）")
    cal = pd.read_parquet(p)
    if only_open and "is_open" in cal.columns:
        cal = cal[cal["is_open"] == 1]
    cal = cal.copy()
    if "cal_date" in cal.columns:
        cal["cal_date"] = cal["cal_date"].astype(str)
    return cal


def us_open_dates(start_date=None, end_date=None):
    """美股区间交易日列表（升序，闭区间）。

    ⚠ 与 open_dates 的区别：用美股日历，不是 A 股日历。
    """
    cal = load_us_cal()
    dates = cal["cal_date"].sort_values()
    if start_date:
        dates = dates[dates >= str(start_date)]
    if end_date:
        dates = dates[dates <= str(end_date)]
    return dates.tolist()


def open_dates_by_market(calendar, start_date=None, end_date=None):
    """按市场名取交易日列表（统一入口）。

    calendar: "SSE"/"SZSE"... → A 股日历；"us" → 美股日历
    """
    if str(calendar).lower() in ("us", "nasdaq", "nyse"):
        return us_open_dates(start_date, end_date)
    return open_dates(start_date=start_date, end_date=end_date,
                      exchange=calendar)
