# -*- coding: utf-8 -*-
"""奇点战法 周线批量回测引擎（纯 numpy，向量化）。

战法定义（老大 2026-09-30 明确）：
    主信号（周线）：买入  KDJ-J <= -5 或 CCI <= -150
                    卖出  KDJ-J >= 110 或 CCI >= 150
    辅助验证（不作主判断）：BOLL 下轨穿越（买侧）/ 上轨穿越（卖侧）
    允许滚仓。

指标参数（与真源 qidian.py 一致）：
    KDJ N=9 M1=3 M2=3（K/D 初值 50，SMA 平滑，J=3K-2D）
    CCI N=20
    BOLL N=20 K=2（标准 2 倍标准差）

执行口径：周线 W-FRI 聚合（open=首 / high=max / low=min / close=末）；
    信号在周线末（周五收盘）判定，买卖按该周收盘价成交。
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def daily_to_weekly(o: np.ndarray, h: np.ndarray, l: np.ndarray,
                    c: np.ndarray, week_end_idx: np.ndarray) -> tuple:
    """日线 → 周线。week_end_idx: 每个交易日归属的周索引（0..n_weeks-1）。"""
    n_weeks = int(week_end_idx[-1]) + 1
    wo = np.full(n_weeks, np.nan)
    wh = np.full(n_weeks, np.nan)
    wl = np.full(n_weeks, np.nan)
    wc = np.full(n_weeks, np.nan)
    for w in range(n_weeks):
        m = week_end_idx == w
        if not m.any():
            continue
        wo[w] = o[m][0]
        wh[w] = h[m].max()
        wl[w] = l[m].min()
        wc[w] = c[m][-1]
    return wo, wh, wl, wc


def _ema_prev(arr: np.ndarray, period: int) -> np.ndarray:
    """SMA(X, N, 1) 的等价递推（α=1/N，初值 50）：用于 K/D 平滑。"""
    out = np.full(len(arr), 50.0)
    alpha = 1.0 / period
    for i in range(1, len(arr)):
        out[i] = out[i - 1] * (1 - alpha) + arr[i] * alpha
    return out


def calc_kdj(h: np.ndarray, l: np.ndarray, c: np.ndarray,
             n: int = 9, m1: int = 3, m2: int = 3) -> np.ndarray:
    """返回 J 序列（周线）。不足 n 根 → J=50。"""
    length = len(c)
    if length < n:
        return np.full(length, 50.0)
    # RSV 向量化
    hw = sliding_window_view(h, n)
    lw = sliding_window_view(l, n)
    hmax = hw.max(axis=1)
    lmin = lw.min(axis=1)
    rng = hmax - lmin
    rsv_full = np.full(length, 50.0)
    rsv = np.where(rng == 0, 50.0, (c[n - 1:] - lmin) / rng * 100.0)
    rsv_full[n - 1:] = rsv
    # K/D 递推（O(n)，初值 50）
    k = np.full(length, 50.0)
    d = np.full(length, 50.0)
    inv_m1 = 1.0 / m1
    inv_m2 = 1.0 / m2
    for i in range(1, length):
        k[i] = k[i - 1] * (1 - inv_m1) + rsv_full[i] * inv_m1
        d[i] = d[i - 1] * (1 - inv_m2) + k[i] * inv_m2
    return 3.0 * k - 2.0 * d


def calc_cci(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 20) -> np.ndarray:
    """返回 CCI 序列（向量化）。不足 n 根 → 0.0。"""
    length = len(c)
    if length < n:
        return np.zeros(length)
    tp = (h + l + c) / 3.0
    tpw = sliding_window_view(tp, n)
    ma = tpw.mean(axis=1)
    md = np.abs(tpw - ma[:, None]).mean(axis=1)
    cci = np.zeros(length)
    val = (tp[n - 1:] - ma) / (0.015 * md)
    val = np.where(md == 0, 0.0, val)
    cci[n - 1:] = val
    return cci


def calc_boll(c: np.ndarray, n: int = 20, k: float = 2.0) -> tuple:
    """返回 (mid, upper, lower) 序列（向量化）。不足 n 根 → NaN。"""
    length = len(c)
    mid = np.full(length, np.nan)
    upper = np.full(length, np.nan)
    lower = np.full(length, np.nan)
    if length < n:
        return mid, upper, lower
    cw = sliding_window_view(c, n)
    m = cw.mean(axis=1)
    s = cw.std(axis=1)
    mid[n - 1:] = m
    upper[n - 1:] = m + k * s
    lower[n - 1:] = m - k * s
    return mid, upper, lower


def backtest_one(o: np.ndarray, h: np.ndarray, l: np.ndarray,
                 c: np.ndarray) -> dict:
    """单标的回测（周线 OHLC 输入）。

    返回 dict：策略累计收益、CAGR、buy&hold 累计/CAGR、超额、胜率、
    最大回撤、空仓比、交易笔数、盈利笔数、每笔收益、BOLL 辅助命中数。
    """
    n = len(c)
    result = {
        "n_weeks": n,
        "strat_ret": np.nan, "strat_cagr": np.nan,
        "bh_ret": np.nan, "bh_cagr": np.nan,
        "excess": np.nan,
        "win_rate": np.nan, "n_trades": 0, "n_wins": 0,
        "max_dd": np.nan, "cash_ratio": np.nan,
        "buy_boll_lower": 0, "sell_boll_upper": 0,
        "trades_ret": [],
    }
    if n < 25:  # 至少 ~半年周线，指标才有意义
        return result

    j = calc_kdj(h, l, c)
    cci = calc_cci(h, l, c)
    mid, upper, lower = calc_boll(c)

    # 信号（主信号，周线末判定）
    buy_sig = (j <= -5.0) | (cci <= -150.0)
    sell_sig = (j >= 110.0) | (cci >= 150.0)

    # BOLL 辅助（穿越）：买侧看下轨穿越（前一周 close > lower 且本周 close < lower）
    lower_cross = np.zeros(n, dtype=bool)
    upper_cross = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if not np.isnan(lower[i]) and not np.isnan(lower[i - 1]):
            lower_cross[i] = c[i - 1] > lower[i - 1] and c[i] < lower[i]
        if not np.isnan(upper[i]) and not np.isnan(upper[i - 1]):
            upper_cross[i] = c[i - 1] < upper[i - 1] and c[i] > upper[i]

    # 滚仓回测（信号周收盘价成交）
    position = False
    entry_price = 0.0
    realized = 1.0       # 已实现净值（平仓时复利）
    equity_curve = []    # 逐周 mark-to-market 净值（含持仓浮盈浮亏）
    trades = []

    for i in range(n):
        if not position and buy_sig[i]:
            position = True
            entry_price = c[i]
            if lower_cross[i]:
                result["buy_boll_lower"] += 1
        elif position and sell_sig[i]:
            ret = c[i] / entry_price - 1.0
            realized *= (1.0 + ret)
            trades.append(ret)
            if upper_cross[i]:
                result["sell_boll_upper"] += 1
            position = False
        mtm = realized * (c[i] / entry_price) if position else realized
        equity_curve.append(mtm)

    # 期末若仍持仓，按末周收盘平仓（未实现收益计入）
    if position:
        ret = c[-1] / entry_price - 1.0
        realized *= (1.0 + ret)
        trades.append(ret)
        position = False

    equity_curve = np.array(equity_curve)
    equity = realized

    # 策略指标
    strat_ret = equity - 1.0
    years = n / 52.0
    strat_cagr = (equity ** (1.0 / years) - 1.0) if equity > 0 and years > 0 else np.nan

    # buy&hold 基准
    bh_ret = c[-1] / c[0] - 1.0
    bh_cagr = ((1.0 + bh_ret) ** (1.0 / years) - 1.0) if (1.0 + bh_ret) > 0 and years > 0 else np.nan

    # 最大回撤（策略净值曲线）
    peak = np.maximum.accumulate(equity_curve)
    dd = (equity_curve - peak) / peak
    max_dd = dd.min()

    # 空仓比：记录逐周持仓状态
    pos_flags = np.zeros(n, dtype=bool)
    pos = False
    for i in range(n):
        if not pos and buy_sig[i]:
            pos = True
        elif pos and sell_sig[i]:
            pos = False
        pos_flags[i] = pos
    cash_ratio = float(1.0 - pos_flags.mean())

    # 胜率
    n_trades = len(trades)
    n_wins = int(sum(1 for t in trades if t > 0))

    result.update({
        "strat_ret": strat_ret, "strat_cagr": strat_cagr,
        "bh_ret": bh_ret, "bh_cagr": bh_cagr,
        "excess": strat_ret - bh_ret,
        "win_rate": (n_wins / n_trades) if n_trades else np.nan,
        "n_trades": n_trades, "n_wins": n_wins,
        "max_dd": max_dd, "cash_ratio": cash_ratio,
        "trades_ret": trades,
    })
    return result


__all__ = ["daily_to_weekly", "calc_kdj", "calc_cci", "calc_boll",
           "backtest_one"]
