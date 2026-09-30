# -*- coding: utf-8 -*-
"""
strategies/qidian.py — 奇点战法信号层
=====================================
本模块承载奇点战法的"标的池 + 数据获取 + 指标计算 + 信号判断"纯逻辑，
供 qidian_daily_scan.py（扫描/推送/报告编排）与其他场景（回测等）复用。

抽取自 qidian_daily_scan.py（2026-09 模块化重构），参数与逻辑保持不变。

信号规则（周线级别）:
  买入: J <= -5 或 CCI <= -150
  卖出: J >= 110 或 CCI >= 150
  双重确认: ETF 和对应指数均触发同一方向信号才推送

数据源:
  ETF日线: tushare pro.fund_daily (优先) → 本地parquet (回退)
  指数日线: tushare pro.index_daily
  外汇/贵金属: tushare pro.fx_daily (XAUUSD.FXCM, bid价口径)
  周线聚合: ISO周(W-FRI)
"""
import os
from datetime import datetime, timedelta
from typing import Dict, Optional

import numpy as np
import pandas as pd

from common.paths import FUND_DAILY_DIR
from fetch.base import get_pro


QIDIAN_POOL = {
    # ── 推荐操作池 14个 ★ ──
    "560700.SH": {"name": "央企股东回报ETF", "recommended": True, "indices": [
        {"code": "932039.CSI", "name": "央企股东回报"},
    ]},
    "512800.SH": {"name": "银行ETF", "recommended": True, "indices": [
        {"code": "399431.SZ", "name": "国证银行"},
        {"code": "000134.SH", "name": "上证银行"},
    ]},
    "515630.SH": {"name": "800证券保险ETF", "recommended": True, "indices": [
        {"code": "931479.CSI", "name": "证券保险"},
    ]},
    "160639.SZ": {"name": "高铁产业LOF", "recommended": True, "indices": [
        {"code": "399807.SZ", "name": "高铁产业"},
    ]},
    "515300.SH": {"name": "300红利低波ETF", "recommended": True, "indices": [
        {"code": "930740.CSI", "name": "300红利低波"},
    ]},
    "159793.SZ": {"name": "线上消费ETF", "recommended": True, "indices": [
        {"code": "931480.CSI", "name": "线上消费"},
    ]},
    "510990.SH": {"name": "180ESG ETF", "recommended": True, "indices": [
        {"code": "931088.CSI", "name": "180ESG"},
    ]},
    "515220.SH": {"name": "中证煤炭ETF", "recommended": True, "indices": [
        {"code": "399998.SZ", "name": "中证煤炭"},
    ]},
    "159995.SZ": {"name": "芯片ETF", "recommended": True, "indices": [
        {"code": "931865.CSI", "name": "半导体"},
    ]},
    "515880.SH": {"name": "通信ETF", "recommended": True, "indices": [
        {"code": "931160.CSI", "name": "通信设备"},
    ]},
    "515580.SH": {"name": "保险ETF", "recommended": True, "indices": [
        {"code": "399809.SZ", "name": "保险主题"},
    ]},
    "512200.SH": {"name": "房地产ETF", "recommended": True, "indices": [
        {"code": "399393.SZ", "name": "房地产"},
    ]},
    "512400.SH": {"name": "有色金属ETF", "recommended": True, "indices": [
        {"code": "399395.SZ", "name": "有色金属"},
    ]},
    "159666.SZ": {"name": "物流ETF", "recommended": True, "indices": [
        {"code": "H30315.CSI", "name": "交通运输"},
    ]},
    # ── 双回测一致优秀 23个 ──
    "517770.SH": {"name": "游戏传媒ETF", "recommended": False, "indices": [
        {"code": "399248.SZ", "name": "文化指数"},
    ]},
    "589070.SH": {"name": "科创芯片设计ETF", "recommended": False, "indices": [
        {"code": "950162.CSI", "name": "科创芯片设计"},
    ]},
    "159530.SZ": {"name": "机器人ETF", "recommended": False, "indices": [
        {"code": "980022.SZ", "name": "机器人产业"},
        {"code": "H30590.CSI", "name": "机器人(国证)"},
    ]},
    "159730.SZ": {"name": "龙头家电ETF", "recommended": False, "indices": [
        {"code": "980028.SZ", "name": "龙头家电"},
    ]},
    "159760.SZ": {"name": "公共卫生ETF", "recommended": False, "indices": [
        {"code": "H30344.CSI", "name": "健康产业"},
    ]},
    "560990.SH": {"name": "科技先锋ETF", "recommended": False, "indices": [
        {"code": "931447.CSI", "name": "科技先锋"},
    ]},
    "515000.SH": {"name": "科技龙头ETF", "recommended": False, "indices": [
        {"code": "931186.CSI", "name": "中证科技"},
    ]},
    "512880.SH": {"name": "全指证券公司ETF", "recommended": False, "indices": [
        {"code": "399975.SZ", "name": "证券公司"},
    ]},
    "159865.SZ": {"name": "畜牧养殖ETF", "recommended": False, "indices": [
        {"code": "930707.CSI", "name": "中证畜牧"},
    ]},
    "562530.SH": {"name": "1000价值稳健ETF", "recommended": False, "indices": [
        {"code": "399631.SZ", "name": "1000价值"},
    ]},
    "159692.SZ": {"name": "证券公司30ETF", "recommended": False, "indices": [
        {"code": "931412.CSI", "name": "证券公司30"},
    ]},
    "515960.SH": {"name": "医药健康100ETF", "recommended": False, "indices": [
        {"code": "931166.CSI", "name": "医药健康100"},
    ]},
    "561500.SH": {"name": "核心竞争力50ETF", "recommended": False, "indices": [
        {"code": "931526.CSI", "name": "企业核心竞争力50"},
    ]},
    "516830.SH": {"name": "300ESG ETF", "recommended": False, "indices": [
        {"code": "931463.CSI", "name": "300ESG"},
    ]},
    "160127.SZ": {"name": "新兴消费LOF", "recommended": False, "indices": [
        {"code": "000942.CSI", "name": "内地消费"},  # 原报告399942.SZ无数据→替换
    ]},
    "159523.SZ": {"name": "300成长创新ETF", "recommended": False, "indices": [
        {"code": "931589.CSI", "name": "300成长创新"},
    ]},
    "159620.SZ": {"name": "500成长创新ETF", "recommended": False, "indices": [
        {"code": "931590.CSI", "name": "500成长创新"},
    ]},
    "516130.SH": {"name": "消费龙头ETF", "recommended": False, "indices": [
        {"code": "931068.CSI", "name": "消费龙头"},
    ]},
    "563980.SH": {"name": "800红利低波ETF", "recommended": False, "indices": [
        {"code": "931848.CSI", "name": "800红利低波"},
    ]},
    "512660.SH": {"name": "军工ETF", "recommended": False, "indices": [
        {"code": "399368.SZ", "name": "国证军工"},
    ]},
    "501030.SH": {"name": "环境治理LOF", "recommended": False, "indices": [
        {"code": "399806.SZ", "name": "环境治理"},
    ]},
    "589720.SH": {"name": "科创创新药ETF", "recommended": False, "indices": [
        {"code": "980086.CNI", "name": "创新药"},
    ]},
    "517160.SH": {"name": "长江保护ETF", "recommended": False, "indices": [
        {"code": "931554.CSI", "name": "长江保护"},
    ]},
    # ── 2026-08-26行业ETF扫描新增 4个（非推荐池）──
    "561910.SH": {"name": "电池ETF", "recommended": False, "indices": [
        {"code": "931719.CSI", "name": "电池主题"},
    ]},
    "159992.SZ": {"name": "创新药ETF", "recommended": False, "indices": [
        {"code": "399993.SZ", "name": "生物医药"},
    ]},
    "159611.SZ": {"name": "电力ETF", "recommended": False, "indices": [
        {"code": "399554.SZ", "name": "中证电力"},
    ]},
    "512980.SH": {"name": "传媒ETF", "recommended": False, "indices": [
        {"code": "399971.SZ", "name": "中证传媒"},
    ]},
    # ── 2026-08-30 可转债ETF新增 2只（非推荐池，奇点战法泛化测试标的）──
    # 注: 511180原配对指数H11049.CSI系错码——tushare与中证官网双重核实该代码实为
    #     "AMAC文体"股票行业指数(基日20081231)，行情数据与转债市场无关（20260903实锤）
    #     511180真实跟踪指数为950041.CSI(上证投资级转债及可交换债)，但tushare中该指数
    #     仅提供close(无OHLC)+数据滞后1日，故双重确认配对000832.CSI(中证转债，OHLC完整)
    "511180.SH": {"name": "可转债ETF海富通", "recommended": False, "indices": [
        {"code": "000832.CSI", "name": "中证转债"},
    ]},
    "511380.SH": {"name": "可转债ETF博时", "recommended": False, "indices": [
        {"code": "931078.CSI", "name": "中证转债及可交换债"},
    ]},
}


# ============================================================
# 1b. 外汇/贵金属现货单标的监控（2026-09-02新增）
# ============================================================
# 格式: fx_code → {name, source}
# 注: XAUUSD.FXCM 为 tushare fx_daily 的伦敦金现（现货黄金）代码，FXCM美元报价口径
# 注: 无对应场内ETF，不参与"ETF+指数"双重确认，单标的周线KDJ/CCI直接评估
XAU_WATCHLIST = {
    "XAUUSD.FXCM": {"name": "伦敦金现(XAU/USD)", "source": "FXCM"},
}


# ============================================================
# 2. 信号阈值常量
# ============================================================
BUY_J_THRESHOLD = -5.0       # KDJ J值买入阈值


BUY_CCI_THRESHOLD = -150.0   # CCI买入阈值


SELL_J_THRESHOLD = 110.0     # KDJ J值卖出阈值


SELL_CCI_THRESHOLD = 150.0   # CCI卖出阈值


KDJ_N = 9       # KDJ RSV窗口(标准9日)


KDJ_M1 = 3      # K平滑


KDJ_M2 = 3      # D平滑


CCI_N = 20      # CCI周期


WEEKS_LOOKBACK = 200  # 获取周线根数(约4年，足够KDJ/CCI计算+历史验证)


# ============================================================
# 3. 数据获取（F1 · 2026-09-25 本地优先改造）
# ------------------------------------------------------------
# 背景：A1 库日更（工作日 17:30 automation 57726fe7）已把 fund_daily/index_daily
#   （core 组）/fx_daily（extend 组）入库，本扫描 18:00 运行时本地数据已就绪——
#   原版 API 优先每日白烧 ~80+ 次调用（41 ETF + 41 指数 + XAU）。
# 新顺序：本地优先（A1 17:30 保证新鲜）→ 本地缺失/不新鲜 → API 回退 →
#   API 也失败时用本地旧数据兜底（比原版更稳，原版 API 失败即空）。
# 新鲜度标准（三类统一）：本地最新 trade_date == end_date。
#   fx 源端 T-1 发布是常态 → 交易日 XAU 常走 API 回退拿最新（每天 1 次，
#   成本可忽略，信号新鲜度优先）；ETF/指数 A1 日更后本地即含当日。
# ============================================================
def _local_fresh(df: pd.DataFrame, end_date: str, tolerance_days: int = 0) -> bool:
    """本地数据新鲜度判定：max(trade_date) 距 end_date 不超过 tolerance_days 自然日。"""
    if df is None or df.empty:
        return False
    max_d = str(df["trade_date"].max())
    if max_d >= end_date:
        return True
    if tolerance_days <= 0:
        return False
    limit = (pd.Timestamp(end_date) - pd.Timedelta(days=tolerance_days)).strftime("%Y%m%d")
    return max_d >= limit


def fetch_etf_daily(code: str, end_date: str, lookback_days: int = 2500) -> pd.DataFrame:
    """
    获取ETF日线数据（本地库优先，A1 日更保证新鲜；缺失时 tushare 回退）
    返回: DataFrame[trade_date, open, high, low, close, vol]
    """
    start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")

    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values("trade_date").reset_index(drop=True)
        # 部分ETF早期数据可能缺high/low，用close填充
        if df["high"].isna().any() or df["low"].isna().any():
            df["high"] = df["high"].fillna(df["close"])
            df["low"] = df["low"].fillna(df["close"])
        return df

    # ── 优先本地库（fund_daily 按年分区，走布局无关的 common.reader 谓词下推）──
    # 行数门槛 len>60 与 API 分支一致——实证（2026-09-25）：511180/511380 本地
    # 仅 29 行（库历史覆盖不全）而 API 有 1476/1572 行，无门槛会静默用劣化数据。
    local_df = pd.DataFrame()
    try:
        from common import reader
        local_df = reader.read_code("fund_daily", code,
                                    columns=["trade_date", "open", "high", "low",
                                             "close", "vol"],
                                    start_date=start_date, end_date=end_date)
        if (local_df is not None and not local_df.empty
                and len(local_df) > 60 and _local_fresh(local_df, end_date)):
            return _normalize(local_df)
    except Exception as e:
        print(f"  [WARN] 本地 fund_daily {code} 读取失败: {e}")

    # ── 回退 tushare（本地缺失/A1 失败或延迟）──
    try:
        pro = get_pro()
        df = pro.fund_daily(ts_code=code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty and len(df) > 60:
            df = df[["trade_date", "open", "high", "low", "close", "vol"]].copy()
            return _normalize(df)
    except Exception as e:
        print(f"  [WARN] tushare fund_daily {code} 失败: {e}")

    # ── 终极兜底：本地旧数据（带 asof 提示）──
    if local_df is not None and not local_df.empty:
        print(f"  [WARN] {code} API 失败，用本地旧数据 asof={local_df['trade_date'].max()}")
        return _normalize(local_df)
    return pd.DataFrame()


def fetch_index_daily(code: str, end_date: str, lookback_days: int = 2500) -> pd.DataFrame:
    """
    获取指数日线数据（本地库优先，A1 日更保证新鲜；缺失时 tushare 回退）
    返回: DataFrame[trade_date, open, high, low, close, vol]
    """
    start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")

    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values("trade_date").reset_index(drop=True)
        # 部分中证指数早期数据无open/high/low（仅有close），用close填充
        if df["open"].isna().any() or df["high"].isna().any() or df["low"].isna().any():
            df["open"] = df["open"].fillna(df["close"])
            df["high"] = df["high"].fillna(df["close"])
            df["low"] = df["low"].fillna(df["close"])
        return df

    # ── 优先本地库（index_daily 按年分区）──
    local_df = pd.DataFrame()
    try:
        from common import reader
        local_df = reader.read_code("index_daily", code,
                                    columns=["trade_date", "open", "high", "low",
                                             "close", "vol"],
                                    start_date=start_date, end_date=end_date)
        if local_df is not None and not local_df.empty:
            if _local_fresh(local_df, end_date):
                return _normalize(local_df)
    except Exception as e:
        print(f"  [WARN] 本地 index_daily {code} 读取失败: {e}")

    # ── 回退 tushare ──
    try:
        pro = get_pro()
        df = pro.index_daily(ts_code=code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            df = df[["trade_date", "open", "high", "low", "close", "vol"]].copy()
            return _normalize(df)
    except Exception as e:
        print(f"  [WARN] tushare index_daily {code} 失败: {e}")

    # ── 终极兜底：本地旧数据 ──
    if local_df is not None and not local_df.empty:
        print(f"  [WARN] {code} API 失败，用本地旧数据 asof={local_df['trade_date'].max()}")
        return _normalize(local_df)
    return pd.DataFrame()


def fetch_fx_daily(code: str, end_date: str, lookback_days: int = 2500) -> pd.DataFrame:
    """
    获取外汇/贵金属现货日线数据（本地库优先，严格新鲜 == end_date；缺失时
    tushare pro.fx_daily 回退，如伦敦金现 XAUUSD.FXCM）
    返回: DataFrame[trade_date, open, high, low, close, vol]
    注: 价格取bid(买价)口径；vol用tick_qty(成交笔数)占位，KDJ/CCI仅用OHLC不受影响。
    fx 新鲜度设计（2026-09-25）：与 ETF/指数同标准（== end_date）——fx 源端
    T-1 发布是常态（本地常缺 end_date 当日），此时回退 API 拿最新；XAU 是唯一
    fx 标的，每天 1 次 API 成本可忽略，信号新鲜度优先。
    """
    start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")

    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.rename(columns={
            "bid_open": "open",
            "bid_close": "close",
            "bid_high": "high",
            "bid_low": "low",
        })
        df["vol"] = df["tick_qty"] if "tick_qty" in df.columns else 0
        df = df[["trade_date", "open", "high", "low", "close", "vol"]].copy()
        return df.sort_values("trade_date").reset_index(drop=True)

    # ── 优先本地库（fx_daily 按年分区，列 bid_*；源端 T-1 常态 → 常走 API 回退）──
    local_df = pd.DataFrame()
    try:
        from common import reader
        local_df = reader.read_code("fx_daily", code,
                                    columns=["trade_date", "bid_open", "bid_high",
                                             "bid_low", "bid_close", "tick_qty"],
                                    start_date=start_date, end_date=end_date)
        if (local_df is not None and not local_df.empty
                and len(local_df) > 60 and _local_fresh(local_df, end_date)):
            return _normalize(local_df)
    except Exception as e:
        print(f"  [WARN] 本地 fx_daily {code} 读取失败: {e}")

    # ── 回退 tushare ──
    try:
        pro = get_pro()
        df = pro.fx_daily(ts_code=code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty and len(df) > 60:
            return _normalize(df)
    except Exception as e:
        print(f"  [WARN] tushare fx_daily {code} 失败: {e}")

    # ── 终极兜底：本地旧数据 ──
    if local_df is not None and not local_df.empty:
        print(f"  [WARN] {code} API 失败，用本地旧数据 asof={local_df['trade_date'].max()}")
        return _normalize(local_df)
    return pd.DataFrame()


# ============================================================
# 4. 日线→ISO周线聚合
# ============================================================
def aggregate_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """
    日线按ISO周聚合为周线（周五为周末，W-FRI）
    返回: DataFrame[week_end_date, open, high, low, close, vol]
    """
    if df.empty:
        return df

    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    # 解析日期
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")

    # ISO周: 周五为周末 → 用 floor("W-FRI") 对齐到本周五
    # pandas的"W-FRI"表示每周以Friday为结束
    df["week_end"] = df["date"].dt.to_period("W-FRI").dt.end_time.dt.normalize()

    # 聚合
    weekly = df.groupby("week_end").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        vol=("vol", "sum"),
    ).reset_index()

    weekly.rename(columns={"week_end": "week_end_date"}, inplace=True)
    # 转回字符串日期方便处理
    weekly["week_end_date"] = weekly["week_end_date"].dt.strftime("%Y%m%d")

    return weekly


# ============================================================
# 6. CCI计算（标准公式）
# ============================================================
def calc_cci(high: np.ndarray, low: np.ndarray, close: np.ndarray,
             n: int = CCI_N) -> float:
    """
    CCI(Commodity Channel Index) 标准计算
    TP = (High + Low + Close) / 3
    CCI = (TP - MA(TP, n)) / (0.015 * Mean_Deviation(TP, n))

    返回最新一根K线的CCI值
    """
    length = len(close)
    if length < n:
        return 0.0

    tp = (high + low + close) / 3.0

    # 最新n期的TP
    tp_n = tp[-n:]
    ma_tp = np.mean(tp_n)
    mean_dev = np.mean(np.abs(tp_n - ma_tp))

    if mean_dev == 0:
        return 0.0

    cci = (tp[-1] - ma_tp) / (0.015 * mean_dev)
    return cci


# ============================================================
# 7. 信号判断
# ============================================================
def check_signal(j: float, cci: float) -> Optional[str]:
    """
    判断买卖信号
    买入: J <= -5 或 CCI <= -150
    卖出: J >= 110 或 CCI >= 150
    返回: "买入" / "卖出" / None
    """
    triggers = []
    if j <= BUY_J_THRESHOLD:
        triggers.append(f"J={j:.1f}<= -5")
    if cci <= BUY_CCI_THRESHOLD:
        triggers.append(f"CCI={cci:.1f}<= -150")
    if j >= SELL_J_THRESHOLD:
        triggers.append(f"J={j:.1f}>=110")
    if cci >= SELL_CCI_THRESHOLD:
        triggers.append(f"CCI={cci:.1f}>=150")

    if any("买入" in t or "<=" in t for t in triggers):
        if j <= BUY_J_THRESHOLD or cci <= BUY_CCI_THRESHOLD:
            return "买入"
    if any(">=" in t for t in triggers):
        if j >= SELL_J_THRESHOLD or cci >= SELL_CCI_THRESHOLD:
            return "卖出"
    return None


def check_signal_detail(j: float, cci: float) -> Dict:
    """
    详细信号判断，返回完整信息
    """
    result = {
        "signal": None,       # "买入" / "卖出" / None
        "triggers": [],       # 触发原因列表
        "j": j,
        "cci": cci,
    }

    buy_triggers = []
    sell_triggers = []

    if j <= BUY_J_THRESHOLD:
        buy_triggers.append(f"J={j:.1f}≤{BUY_J_THRESHOLD:.0f}")
    if cci <= BUY_CCI_THRESHOLD:
        buy_triggers.append(f"CCI={cci:.1f}≤{BUY_CCI_THRESHOLD:.0f}")
    if j >= SELL_J_THRESHOLD:
        sell_triggers.append(f"J={j:.1f}≥{SELL_J_THRESHOLD:.0f}")
    if cci >= SELL_CCI_THRESHOLD:
        sell_triggers.append(f"CCI={cci:.1f}≥{SELL_CCI_THRESHOLD:.0f}")

    if buy_triggers:
        result["signal"] = "买入"
        result["triggers"] = buy_triggers
    elif sell_triggers:
        result["signal"] = "卖出"
        result["triggers"] = sell_triggers

    return result
