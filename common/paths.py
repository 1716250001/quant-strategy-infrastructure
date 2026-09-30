# -*- coding: utf-8 -*-
"""
common/paths.py — 全项目路径的唯一解析入口
==========================================
设计原则：
  - 所有路径最终来源于 config.py 的 PROJECT_DIR，不做二次推算
  - tools/ 下脚本禁止再用 os.path.dirname(__file__) + ".." 猜路径
  - 派生目录（日报/背离信号）在此集中定义，避免散落各处

用法：
    from common.paths import DATA_DIR, REPORT_DIR, ensure_dir, data_file
"""
import os

from config import (
    PROJECT_DIR,
    CODE_DIR,
    DATA_DIR,
    DOC_DIR,
    TEMP_DIR,
    LOG_DIR,
    SIGNALS_DIR,
    MARKET_DATA_DIR,
    META_DIR,
    STOCK_DAILY_DIR,
    STOCK_DAILY_BASIC_DIR,
    FUND_DAILY_DIR,
    FUND_NAV_DIR,
    CB_DAILY_DIR,
    ADJ_FACTOR_DIR,
    INDEX_DAILY_DIR,
    STK_LIMIT_DIR,
    MARKET_STATE_DIR,
    MARKET_STATE_DERIVED_DIR,
)

# ── 派生目录（此前由各脚本自行推算）─────────────────────────
REPORT_DIR = os.path.join(DOC_DIR, "日报")                 # HTML 日报输出
DIVERGENCE_DIR = os.path.join(SIGNALS_DIR, "divergence")   # 背离候选池/触发信号

__all__ = [
    "PROJECT_DIR", "CODE_DIR", "DATA_DIR", "DOC_DIR", "TEMP_DIR", "LOG_DIR", "SIGNALS_DIR",
    "MARKET_DATA_DIR", "META_DIR", "STOCK_DAILY_DIR", "STOCK_DAILY_BASIC_DIR",
    "FUND_DAILY_DIR", "FUND_NAV_DIR", "CB_DAILY_DIR", "ADJ_FACTOR_DIR",
    "INDEX_DAILY_DIR", "STK_LIMIT_DIR", "MARKET_STATE_DIR",
    "MARKET_STATE_DERIVED_DIR", "REPORT_DIR", "DIVERGENCE_DIR",
    "ensure_dir", "data_file", "report_file", "compact_date", "pretty_date",
]


def ensure_dir(path):
    """确保目录存在，并原样返回该路径（便于链式调用）"""
    os.makedirs(path, exist_ok=True)
    return path


def compact_date(date_str):
    """统一为 YYYYMMDD 紧凑格式（容忍 YYYY-MM-DD）"""
    return str(date_str).replace("-", "")


def pretty_date(date_str):
    """统一为 YYYY-MM-DD 可读格式（容忍 YYYYMMDD）"""
    d = compact_date(date_str)
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return str(date_str)


def data_file(date_str, prefix="report_data", suffix=".json"):
    """拼接 daily_data 下的日期文件名。

    data_file('20260914', 'report_data') -> .../daily_data/report_data_20260914.json
    """
    return os.path.join(DATA_DIR, f"{prefix}_{compact_date(date_str)}{suffix}")


def report_file(tformat, suffix="-量化日报.html"):
    """日报输出路径（tformat 形如 2026-09-14）"""
    return os.path.join(REPORT_DIR, f"{tformat}{suffix}")
