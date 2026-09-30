# -*- coding: utf-8 -*-
"""
common/codes.py — 证券代码格式转换
===================================
统一此前 3 套分散实现：
  - fetch/base.py: etf_to_ts_code / stock_to_ts_code
  - push/kline_source.py: _sina_symbol（secid → 新浪）
  - tools/divergence_trigger.py: ts_code_to_secid / ts_code_to_sina_symbol
    （该模块已于 2026-09-24 随背离扫描器归档至 _archive/背离扫描器-20260924/，此处仅记来源）
"""


def _zfill6(code):
    return str(code).zfill(6)


def etf_to_ts_code(code):
    """6 位 ETF/LOF 代码 → Tushare 格式。

    5 开头 → .SH；1 开头 → .SZ；其余按沪市处理。
    """
    c = _zfill6(code)
    if c.startswith("5"):
        return f"{c}.SH"
    if c.startswith("1"):
        return f"{c}.SZ"
    return f"{c}.SH"


def stock_to_ts_code(code):
    """6 位股票代码 → Tushare 格式（沪/深/北交所）。"""
    c = _zfill6(code)
    if c.startswith(("60", "68", "90", "11", "13")):
        return f"{c}.SH"
    if c.startswith(("00", "30", "20", "15", "16", "18")):
        return f"{c}.SZ"
    if c.startswith(("43", "83", "87", "88")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def to_secid(ts_code):
    """Tushare 代码 → 东财 secid。

    600519.SH → 1.600519（沪市）；000001.SZ → 0.000001（深市/北交所）。
    """
    s = str(ts_code)
    if "." not in s:
        return None
    code, market = s.split(".")
    if market == "SH":
        return f"1.{code}"
    if market in ("SZ", "BJ"):
        return f"0.{code}"
    return None


def to_sina_symbol(code_or_secid):
    """Tushare 代码 或 东财 secid → 新浪 symbol。

    600519.SH → sh600519；0.399001 → sz399001
    """
    s = str(code_or_secid)
    parts = s.split(".")
    if len(parts) != 2:
        return None
    a, b = parts
    # 形式一: ts_code (600519.SH)
    if b in ("SH", "SZ"):
        return ("sh" if b == "SH" else "sz") + a
    # 形式二: secid (1.600519 / 0.399001)
    if a in ("0", "1"):
        return ("sh" if a == "1" else "sz") + b
    return None
