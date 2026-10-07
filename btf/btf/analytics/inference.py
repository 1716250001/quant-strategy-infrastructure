# -*- coding: utf-8 -*-
"""推断层工具（**RC-06，2026-10-05**）：夏普显著性与多重检验校正的**可见化**。

设计纪律（ADR-13）：
    本模块**只输出数字**，不替用户判定"策略有效 / 无效"。它要回答的是一个
    更朴素的问题——「**试得越多，同样的夏普越不可信**」，并把这个事实变成
    产物里可读的数（`N` / PSR / DSR），供框架外的推断使用。

口径（Bailey & López de Prado 2012，与 Lo 2002 同族；`n_periods` 即文献中的 `T`）：
    - 夏普 `sr` 为**同频率（单期）**值；年化夏普须先 `/√annualization`
      （便捷换算见 `annualized_to_period`）。
    - `skew` = 三阶标准化矩；`kurt` = 四阶标准化矩（正态 = 3，**非超额峰度**）。
    - 夏普标准误（PSR 的方差来源）：
        SE(sr) = √( (1 − skew·sr + (kurt − 1)/4 · sr²) / (n_periods − 1) )
    - PSR(sr*) = Φ( (sr − sr*) / SE(sr) )——"真实夏普 > sr*"的置信度。
    - DSR = PSR(sr* = E[max sr | N 次独立试验])，其中
        E[max sr] ≈ √Var(sr) · [ (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) ]
        γ = 0.5772156649（Euler–Mascheroni）；N = 1 时退化为 PSR(sr*=0)。

依赖：仅标准库（`math` / `statistics`）——便于单测与复算，不引入 scipy。
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

__all__ = [
    "annualized_to_period",
    "deflated_sharpe",
    "dsr_from_returns",
    "expected_max_sharpe",
    "probabilistic_sharpe",
    "psr_from_returns",
    "sharpe_moments",
    "sharpe_stderr",
]

_EULER = 0.5772156649015329


# ── 正态分布（避免引入 scipy）──
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """标准正态分位（Acklam 近似，|误差| ≲ 1.15e-9）。"""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p 须 ∈ (0,1)，得 {p!r}")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q
                + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q
                 + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    num = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    den = (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    return num / den


# ── 公共 API ──
def annualized_to_period(sr_annual: float, *, annualization: int = 252) -> float:
    """年化夏普 → 单期夏普（PSR/DSR 的输入口径）。"""
    if annualization <= 0:
        raise ValueError(f"annualization 须 > 0，得 {annualization!r}")
    return float(sr_annual) / math.sqrt(annualization)


def sharpe_stderr(sr: float, *, n_periods: int, skew: float = 0.0,
                  kurt: float = 3.0) -> float:
    """夏普标准误（含偏度 / 峰度修正；B&LdP 方差式）。

    `n_periods` 为**样本期数**（如日收益样本数）；`sr` 为单期夏普。
    """
    if n_periods < 2:
        raise ValueError(f"n_periods 须 ≥ 2（方差需 n−1），得 {n_periods!r}")
    var = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / (n_periods - 1)
    if var <= 0:
        raise ValueError(f"夏普方差非正（skew={skew}, kurt={kurt}, sr={sr}）")
    return math.sqrt(var)


def probabilistic_sharpe(sr: float, *, sr0: float = 0.0, n_periods: int,
                         skew: float = 0.0, kurt: float = 3.0) -> float:
    """PSR：`P(真实单期夏普 > sr0)`（Bailey & López de Prado）。

    返回 ∈ (0,1)；**越大越显著**。典型判读阈值 0.95（但多重检验下须改用 DSR）。
    """
    se = sharpe_stderr(sr, n_periods=n_periods, skew=skew, kurt=kurt)
    return _norm_cdf((sr - sr0) / se)


def expected_max_sharpe(*, n_periods: int, skew: float = 0.0,
                        kurt: float = 3.0, sr: float = 0.0,
                        n_trials: int) -> float:
    """N 次试验下**最大夏普的期望**（多重检验基准）。

    `n_trials ≤ 1` ⇒ 返回 0（无校正）。
    """
    if n_trials <= 1:
        return 0.0
    se = sharpe_stderr(sr, n_periods=n_periods, skew=skew, kurt=kurt)
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return se * ((1.0 - _EULER) * z1 + _EULER * z2)


def deflated_sharpe(sr: float, *, n_periods: int, skew: float = 0.0,
                    kurt: float = 3.0, n_trials: int = 1) -> float:
    """DSR：以"N 次试验的最大夏普期望"为基准的 PSR。

    **DSR 才是"挑出来"的夏普的正确显著性**（PSR 只看单个策略，不看挑选过程）。
    """
    if n_trials <= 1:
        return probabilistic_sharpe(sr, sr0=0.0, n_periods=n_periods,
                                    skew=skew, kurt=kurt)
    sr0 = expected_max_sharpe(n_periods=n_periods, skew=skew, kurt=kurt, sr=sr,
                              n_trials=n_trials)
    return probabilistic_sharpe(sr, sr0=sr0, n_periods=n_periods,
                               skew=skew, kurt=kurt)


def sharpe_moments(returns: Sequence[float]) -> tuple[int, float, float, float]:
    """收益序列 → `(样本期数, 单期夏普, 偏度, 峰度)`（一次式矩计算）。"""
    r = [float(x) for x in returns]
    n = len(r)
    if n < 3:
        raise ValueError(f"样本须 ≥ 3（偏度/峰度需三阶以上矩），得 {n}")
    m = statistics.fmean(r)
    dev = [x - m for x in r]
    m2 = statistics.fmean([d * d for d in dev])
    if m2 <= 0.0:
        raise ValueError("收益方差为 0：夏普无定义（PSR/DSR 不适用）")
    sd = math.sqrt(m2)
    skew = statistics.fmean([d ** 3 for d in dev]) / m2 ** 1.5
    kurt = statistics.fmean([d ** 4 for d in dev]) / m2 ** 2
    return n, m / sd, skew, kurt


def psr_from_returns(returns: Sequence[float], *, sr0: float = 0.0) -> float:
    """便捷式：原始收益序列 → PSR（内部算矩）。"""
    n, sr, skew, kurt = sharpe_moments(returns)
    return probabilistic_sharpe(sr, sr0=sr0, n_periods=n, skew=skew, kurt=kurt)


def dsr_from_returns(returns: Sequence[float], *, n_trials: int) -> float:
    """便捷式：原始收益序列 → DSR（`n_trials` = 含"历史累计试验"的总试验数）。"""
    n, sr, skew, kurt = sharpe_moments(returns)
    return deflated_sharpe(sr, n_periods=n, skew=skew, kurt=kurt,
                           n_trials=n_trials)
