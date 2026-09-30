# -*- coding: utf-8 -*-
"""
indicators.py — 三层金字塔轮动策略 · 技术指标库
================================================
所有技术指标计算函数集中于此，消除跨文件重复定义。

包含:
  - 涨跌幅计算 (calc_chg, calc_chg_from_arr)
  - 量比 (calc_vr)
  - 量价四象限 (quadrant)
  - β因子评分 (beta_score, beta_to_status)
  - EMA (numpy数组版)
  - BBI短周期标量版 (calc_bbi_value) + BBI长周期序列版 (calc_bbi_series)
  - MACD DataFrame版 (calc_macd_df) + 背离检测 (detect_divergence)
  - KDJ (简化版 & 完整版)
  - RSI
  - 布林带
"""

import numpy as np

# ============================================================
# 涨跌幅
# ============================================================
def calc_chg(close_arr, window=5):
    """window日涨跌幅（基于数组），返回百分比"""
    if len(close_arr) < window + 1:
        return np.nan
    return (close_arr[-1] / close_arr[-(window + 1)] - 1) * 100


def calc_chg_from_arr(arr, window):
    """同 calc_chg，别名（兼容旧调用）"""
    return calc_chg(arr, window)


# ============================================================
# 量比
# ============================================================
def calc_vr(df, vol_col="vol", short=5, long=20):
    """
    量比 = 近short日均量 / 近long日均量
    df: pandas DataFrame，需包含 vol_col 列
    """
    if len(df) < long:
        return np.nan
    try:
        vol_short = df[vol_col].tail(short).mean()
        if long > short:
            vol_long = df[vol_col].head(len(df) - short).tail(long - short).mean()
        else:
            vol_long = df[vol_col].head(long).mean()
        if vol_long == 0:
            return 1.0
        return vol_short / vol_long
    except Exception:
        return np.nan


# ============================================================
# 量价四象限
# ============================================================
def quadrant(chg, vr):
    """
    量价四象限判定
    ①量价齐升  ②筑底吸筹  ③量缩价涨  ④量价齐跌
    """
    if chg is None or np.isnan(chg) or vr is None or np.isnan(vr):
        return "—"
    if chg > 2 and vr > 1.05:
        return "①量价齐升"
    if -2 <= chg <= 2 and vr > 1.05:
        return "②筑底吸筹"
    if chg < -2 and vr > 1.05:
        return "④量价齐跌"
    if chg > 2 and vr < 0.95:
        return "③量缩价涨"
    if chg < -2 and vr < 0.95:
        return "④量价齐跌"
    if chg > 2:
        return "①量价齐升"
    if chg > 0.5:
        return "①量价齐升"
    if chg < -2:
        return "④量价齐跌"
    if chg < -0.5:
        return "④量价齐跌"
    if abs(chg) <= 0.5 and vr > 1.0:
        return "②筑底吸筹"
    if abs(chg) <= 0.5 and vr < 1.0:
        return "④量价齐跌"
    return "—"


# ============================================================
# β因子评分
# ============================================================
def beta_score(chg, vr, dchg):
    """
    三因子评分: 价格趋势 + 量价组合 + 当日涨跌
    范围 [-6, +6]
    """
    s = 0
    if chg is not None and not np.isnan(chg):
        if chg > 1.5:
            s += 2
        elif chg > 0.3:
            s += 1
        elif chg < -1.5:
            s -= 2
        elif chg < -0.3:
            s -= 1
    if vr is not None and not np.isnan(vr) and chg is not None:
        if chg > 0 and vr > 1.05:
            s += 1
        elif chg < 0 and vr > 1.05:
            s -= 1
        elif chg > 0 and vr < 0.95:
            s -= 1
        elif chg < 0 and vr < 0.95:
            s -= 1
    if dchg is not None and not np.isnan(dchg):
        if dchg > 4:
            s += 2
        elif dchg > 2:
            s += 1
        elif dchg < -4:
            s -= 2
        elif dchg < -2:
            s -= 1
    return max(-6, min(6, s))


def beta_to_status(beta):
    """β(±10量级) → 市场状态 + 建议仓位

    ⚠ 已废弃（deprecated）：本函数阈值按 ±10 量级标定，不适用于 ±5 量级的
      「八指数β均值」；当前零调用，请勿新接入。三套口径的正确归属：
        - 八指数β均值(±5量级) → fetch/market_beta.py 内专用阈值（仅参考，无决策下游）
        - 四因子β(±10量级，决策口径) → composite_beta_to_status / composite_beta_to_position
    """
    if beta is None:
        return "未知", "—"
    if beta >= 3:
        return "强势", "115%"
    if beta >= 1:
        return "偏强", "95%"
    if beta >= -1:
        return "中性", "90%"
    if beta >= -3:
        return "偏弱", "70%"
    return "弱势", "50%"


# ============================================================
# 四因子 composite_beta → 状态/仓位映射 (v3.0+)
# ============================================================
def composite_beta_to_status(b):
    """composite_beta → 共振状态描述 (±10量级)"""
    if b is None:
        return "未知"
    if b >= 6:  return "强势共振"
    if b >= 3:  return "偏多共振"
    if b >= 1:  return "中性偏多"
    if b >= -1: return "中性"
    if b >= -3: return "偏空共振"
    if b >= -6: return "偏空共振"
    return "弱势共振"


def composite_beta_to_position(b):
    """composite_beta → 建议仓位 (字符串, ±10量级)"""
    if b is None:
        return "中性90%"
    if b >= 6:  return "115%"
    if b >= 3:  return "105%"
    if b >= 1:  return "95%"
    if b >= -1: return "90%"
    if b >= -3: return "75%（禁止开新仓）"
    if b >= -6: return "55%（禁止开新仓）"
    return "≤40%（生存模式）"


# ============================================================
# 杠杆热度连续值（单一真源）
# ============================================================
def leverage_heat_continuous(b_pct):
    """两融 B 口径占比(%) → 杠杆热度连续值，值域 [-1, +2]

    ⚠ 单一真源：fetch/leverage.py（采集期）与 tools/fix_and_resonance.py（合成期）
      必须共用本函数，禁止任一侧再内联映射（历史上两侧各写一套，
      采集期用 lev_sub_score*2 把值域放大到 [-2,+4]，导致 compose 时
      杠杆项独占 beta_4=4.0 并撞上 ±10 上限被截断）。

    标定点：b_pct=10% → 0（中性）；20% → +1；≥30% → +2（顶格）。
    标定点与两融红警阈值（≥20% 熔断）对齐，故 20% 恰好对应 +1。
    """
    if b_pct is None or b_pct <= 0:
        return 0.0
    return round(max(-1.0, min(2.0, b_pct / 20 * 2 - 1)), 1)


# ============================================================
# EMA
# ============================================================
def ema(arr, period):
    """指数移动平均"""
    k = 2 / (period + 1)
    result = np.full_like(arr, np.nan, dtype=float)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1 - k)
    return result


# ============================================================
# MACD (numpy数组版, 复用ema)
# ============================================================
def calc_macd_arr(close_arr, fast=12, slow=26, signal=9):
    """
    MACD指标计算 (numpy数组版), 返回 (dif, dea, hist) 三个数组
    适用于无DataFrame的场景 (如wide_etf.py中直接操作close数组)

    参数:
        close_arr: numpy 1D 数组 (收盘价)
        fast/slow/signal: MACD标准参数 (12/26/9)
    返回: (dif, dea, hist) 元组, hist = 2*(dif-dea)
    """
    ema_fast = ema(close_arr, fast)
    ema_slow = ema(close_arr, slow)
    dif = ema_fast - ema_slow
    dea = ema(dif, signal)
    hist = 2 * (dif - dea)
    return dif, dea, hist


# ============================================================
# BBI (多空指标) — 两套定义, 用函数名区分
# ============================================================
def calc_bbi_value(close_arr):
    """
    BBI短周期标量版: (MA3 + MA6 + MA12 + MA24) / 4
    返回最新BBI值 (标量), 适用于日线日报ETF指标计算
    与 calc_bbi_series(periods=(13,21,34,55)) 是两套不同均线周期, 不可混用
    """
    n = len(close_arr)
    ma_list = []
    for period in (3, 6, 12, 24):
        if n >= period:
            ma_list.append(np.mean(close_arr[-period:]))
    return np.mean(ma_list) if ma_list else np.nan


def calc_bbi_series(df, periods=(13, 21, 34, 55)):
    """
    BBI长周期序列版: 多条MA的算术平均, 在df中添加 'BBI' 列
    适用于盘中实时监控 (MA13/21/34/55 长周期)
    参数:
        df: 含 'close' 列的 DataFrame
        periods: 均线周期元组, 默认 (13, 21, 34, 55)
    返回: df (含新增 BBI 列)
    """
    ma_sum = sum(df["close"].rolling(p).mean() for p in periods)
    df["BBI"] = ma_sum / len(periods)
    return df


# ============================================================
# MACD (DataFrame版, pandas EWM实现)
# ============================================================
def calc_macd_df(df, fast=12, slow=26, signal=9, add_mom_ratio=False):
    """
    MACD指标计算 (DataFrame版), 在df中添加 DIF/DEA/MACD 列
    与 ema() (numpy数组版) 功能等价, 接口不同: 此版直接操作DataFrame

    参数:
        df: 含 'close' 列的 DataFrame
        fast/slow/signal: MACD标准参数 (12/26/9)
        add_mom_ratio: 是否添加动能收益率 MOM_RATIO = MACD柱/close*100
                       (盘中背离监控需要, 日线日报不需要)
    返回: df (含新增列)
    """
    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = (dif - dea) * 2

    df["DIF"] = dif
    df["DEA"] = dea
    df["MACD"] = macd_bar

    if add_mom_ratio:
        df["MOM_RATIO"] = (macd_bar / close) * 100

    return df


# ============================================================
# MACD & 背离检测
# ============================================================
def detect_divergence(price, dif):
    """顶/底背离检测（20日窗口）"""
    if len(price) < 20:
        return "—"
    recent_p = price[-20:]
    recent_d = dif[-20:]
    p_high = recent_p[-1] >= np.max(recent_p[:-1])
    d_weaker = recent_d[-1] < np.max(recent_d[:-1]) * 0.9
    if p_high and d_weaker:
        return "顶背离"
    p_low = recent_p[-1] <= np.min(recent_p[:-1])
    d_stronger = recent_d[-1] > np.min(recent_d[:-1]) * 1.1
    if p_low and d_stronger:
        return "底背离"
    return "—"


# ============================================================
# KDJ
# ============================================================
def calc_kdj(high=None, low=None, close=None, n=14, smooth=False,
             m1=3, m2=3):
    """
    KDJ指标计算 (统一版)

    参数:
        high/low/close: numpy 1D 数组 (close 必填; high/low 可选, 无则用close)
        n: RSV计算周期 (默认14)
        smooth: False=仅返回RSV (兼容旧calc_kdj_simple/full);
                True=带SMA平滑 (标准KDJ, K=SMA(RSV,m1), D=SMA(K,m2), J=3K-2D)
        m1/m2: SMA平滑周期 (仅smooth=True时生效)
    返回: (K, D, J) 标量元组
    """
    if close is None:
        return 50.0, 50.0, 50.0
    if high is None:
        high = close
    if low is None:
        low = close
    if len(close) < n:
        return 50.0, 50.0, 50.0

    low_n = np.min(low[-n:])
    high_n = np.max(high[-n:])
    rsv = (close[-1] - low_n) / (high_n - low_n) * 100 if high_n != low_n else 50.0

    if not smooth:
        # 简化版: K=D=J=RSV
        return rsv, rsv, rsv

    # 标准版: 需要计算完整序列的RSV再做SMA平滑
    length = len(close)
    rsv_arr = np.full(length, 50.0)
    for i in range(n - 1, length):
        llv = np.min(low[i - n + 1:i + 1])
        hhv = np.max(high[i - n + 1:i + 1])
        if hhv != llv:
            rsv_arr[i] = (close[i] - llv) / (hhv - llv) * 100.0
        else:
            rsv_arr[i] = 50.0

    k_arr = np.full(length, 50.0)
    d_arr = np.full(length, 50.0)
    for i in range(1, length):
        k_arr[i] = (k_arr[i - 1] * (m1 - 1) + rsv_arr[i]) / m1
        d_arr[i] = (d_arr[i - 1] * (m2 - 1) + k_arr[i]) / m2

    j_arr = 3.0 * k_arr - 2.0 * d_arr
    return k_arr[-1], d_arr[-1], j_arr[-1]


# 兼容别名
def calc_kdj_simple(close, n=14):
    """简化KDJ（仅用收盘价, 无SMA平滑）— 兼容旧调用"""
    return calc_kdj(close=close, n=n, smooth=False)


def calc_kdj_full(high, low, close, n=14):
    """完整KDJ（用最高/最低/收盘价, 无SMA平滑）— 兼容旧调用"""
    return calc_kdj(high=high, low=low, close=close, n=n, smooth=False)


def calc_kdj_standard(high, low, close, n=14, m1=3, m2=3):
    """标准KDJ（带SMA平滑, J=3K-2D）— 兼容旧调用"""
    return calc_kdj(high=high, low=low, close=close, n=n, smooth=True, m1=m1, m2=m2)


# ============================================================
# RSI
# ============================================================
def calc_rsi(close, n=14):
    """RSI(14)"""
    if len(close) < n + 1:
        return np.nan
    deltas = np.diff(close[-n - 1:])
    gain = np.mean(deltas[deltas > 0]) if np.any(deltas > 0) else 0
    loss = -np.mean(deltas[deltas < 0]) if np.any(deltas < 0) else 1e-9
    return 100 - 100 / (1 + gain / loss)


# ============================================================
# 布林带
# ============================================================
def calc_bollinger(close, n=20):
    """布林带 (20,2)"""
    if len(close) < n:
        return {"upper": np.nan, "mid": np.nan, "lower": np.nan, "position": "—"}
    mid = np.mean(close[-n:])
    std = np.std(close[-n:])
    upper = mid + 2 * std
    lower = mid - 2 * std
    pct = (close[-1] - lower) / (upper - lower) * 100 if upper != lower else 50
    if pct > 80:
        pos = "超买区"
    elif pct < 20:
        pos = "超卖区"
    else:
        pos = "正常"
    return {"upper": upper, "mid": mid, "lower": lower, "position": f"{pct:.0f}% {pos}"}
