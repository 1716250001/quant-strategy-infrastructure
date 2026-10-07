# -*- coding: utf-8 -*-
"""蒙特卡洛 / 置换检验工具（**RC-16**，2026-10-05；派单 §5）。

纪律（逐条落地）：
    - **只作用在收益/成交序列层**：本模块**不进入引擎、不重跑撮合**——
      "撮合规则逐位不变"由"扰动发生在引擎之外"结构性成立（09 #12）；
    - **一切随机经显式种子**（`random.Random(seed)`，不用全局 rng）：
      同种子重跑**逐位一致**（确定性纪律）；
    - 结果以**分布**披露（p05/p50/p95 + 置信区间是否跨 0），
      **不得只报最优**；`p_value` 为**双侧**（|·| 比较）；
    - 只输出数字，不判定策略有效 / 无效（ADR-13）。

统计量口径（输入 = 单期日收益序列；与 `analytics/inference` 同族）：
    sharpe            = mean/std × √annualization（**年化**）
    annualized_return = (∏(1+r))^(annualization/n) − 1（几何年化）
    max_drawdown      = min(nav/peak − 1)（≤ 0；路径敏感——`order` 置换对其有意义）
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any

__all__ = [
    "annualized_return",
    "annualized_sharpe",
    "bootstrap_ci",
    "distribution_summary",
    "max_drawdown",
    "noise_baseline",
    "percentile",
    "permutation_pvalue",
]

#: 支持的统计量名（白名单——未知名即报错，不静默退化为别的量）
STATISTICS = ("sharpe", "annualized_return", "max_drawdown")


def _clean(returns: Sequence[float]) -> list[float]:
    return [float(x) for x in returns if x is not None and float(x) == float(x)]


def percentile(values: Sequence[float], q: float) -> float:
    """最近秩分位（q ∈ [0,1]；无插值——小样本下更保守）。"""
    vals = sorted(float(v) for v in values)
    if not vals:
        raise ValueError("percentile: 空序列")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q 须 ∈ [0,1]，得 {q!r}")
    idx = max(0, min(len(vals) - 1, math.ceil(q * len(vals)) - 1))
    return vals[idx]


def annualized_sharpe(returns: Sequence[float], *,
                      annualization: int = 252) -> float:
    """年化夏普（n<2 或 std=0 ⇒ NaN——不可算项不得填 0）。"""
    r = _clean(returns)
    n = len(r)
    if n < 2:
        return float("nan")
    m = sum(r) / n
    var = sum((x - m) ** 2 for x in r) / (n - 1)
    if var <= 0:
        return float("nan")
    return m / math.sqrt(var) * math.sqrt(annualization)


def annualized_return(returns: Sequence[float], *,
                      annualization: int = 252) -> float:
    """几何年化收益（∏(1+r) ≤ 0 ⇒ NaN）。"""
    r = _clean(returns)
    if not r:
        return float("nan")
    growth = 1.0
    for x in r:
        growth *= 1.0 + x
    if growth <= 0:
        return float("nan")
    return growth ** (annualization / len(r)) - 1.0


def max_drawdown(returns: Sequence[float]) -> float:
    """最大回撤（≤ 0；路径敏感）。"""
    nav = 1.0
    peak = 1.0
    mdd = 0.0
    for x in _clean(returns):
        nav *= 1.0 + x
        peak = max(peak, nav)
        if peak > 0:
            mdd = min(mdd, nav / peak - 1.0)
    return mdd


def _statistic(name: str, returns: Sequence[float],
               annualization: int) -> float:
    if name == "sharpe":
        return annualized_sharpe(returns, annualization=annualization)
    if name == "annualized_return":
        return annualized_return(returns, annualization=annualization)
    if name == "max_drawdown":
        return max_drawdown(returns)
    raise ValueError(f"statistic 须 ∈ {STATISTICS}，得 {name!r}")


def distribution_summary(values: Sequence[float]) -> dict[str, Any]:
    """分布摘要（p05/p50/p95 + min/max/mean/std；NaN 剔除并披露 n）。"""
    vals = _clean(values)
    if not vals:
        return {"n": 0, "p05": None, "p50": None, "p95": None, "min": None,
                "max": None, "mean": None, "std": None}
    m = sum(vals) / len(vals)
    var = (sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
           if len(vals) > 1 else 0.0)
    return {
        "n": len(vals),
        "p05": percentile(vals, 0.05),
        "p50": percentile(vals, 0.50),
        "p95": percentile(vals, 0.95),
        "min": min(vals),
        "max": max(vals),
        "mean": m,
        "std": math.sqrt(var),
    }


def bootstrap_ci(
    returns: Sequence[float],
    *,
    n_resamples: int = 1000,
    seed: int = 0,
    annualization: int = 252,
    alpha: float = 0.05,
    statistics: Sequence[str] = ("sharpe", "annualized_return"),
) -> dict[str, Any]:
    """iid bootstrap 置信区间（有放回重采样；**同种子逐位一致**）。

    每个统计量给出：`observed` / p05 / p50 / p95 / `ci_low` / `ci_high` /
    `crosses_zero`（区间跨 0 ⇒ "方向不确定"必须如实披露）。
    """
    if n_resamples < 1:
        raise ValueError(f"n_resamples 须 ≥ 1，得 {n_resamples!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha 须 ∈ (0,1)，得 {alpha!r}")
    r = _clean(returns)
    rng = random.Random(f"bootstrap:{int(seed)}")
    out: dict[str, Any] = {"n_resamples": n_resamples, "seed": int(seed),
                           "alpha": alpha, "n_periods": len(r)}
    n = len(r)
    for name in statistics:
        observed = _statistic(name, r, annualization)
        draws: list[float] = []
        for _ in range(n_resamples):
            sample = [r[rng.randrange(n)] for _ in range(n)] if n else []
            draws.append(_statistic(name, sample, annualization))
        summary = distribution_summary(draws)
        lo = percentile([v for v in draws if v == v], alpha / 2) if any(
            v == v for v in draws) else None
        hi = percentile([v for v in draws if v == v], 1 - alpha / 2) if any(
            v == v for v in draws) else None
        out[name] = {
            "observed": observed,
            "p05": summary["p05"], "p50": summary["p50"], "p95": summary["p95"],
            "ci_low": lo, "ci_high": hi,
            "crosses_zero": (None if lo is None or hi is None
                             else bool(lo <= 0.0 <= hi)),
            "n_valid": summary["n"],
        }
    return out


def permutation_pvalue(
    returns: Sequence[float],
    *,
    scheme: str = "sign",
    statistic: str = "sharpe",
    n_permutations: int = 1000,
    seed: int = 0,
    annualization: int = 252,
) -> dict[str, Any]:
    """置换检验（**双侧** p 值 = P(|null| ≥ |observed|)）。

    `scheme="sign"`  随机翻转每日收益符号——"收益只是零均值噪声吗"（对均值/
    夏普类统计量有意义）；`scheme="order"` 打乱收益顺序——对**路径敏感**统计量
    （如 `max_drawdown`）有意义（顺序置换对夏普是退化的：均值/标准差不变）。
    """
    if scheme not in ("sign", "order"):
        raise ValueError(f"scheme 须为 sign|order，得 {scheme!r}")
    if n_permutations < 1:
        raise ValueError(f"n_permutations 须 ≥ 1，得 {n_permutations!r}")
    r = _clean(returns)
    observed = _statistic(statistic, r, annualization)
    rng = random.Random(f"perm:{scheme}:{statistic}:{int(seed)}")
    null: list[float] = []
    for _ in range(n_permutations):
        if scheme == "sign":
            sample = [x * (1.0 if rng.random() < 0.5 else -1.0) for x in r]
        else:
            sample = list(r)
            rng.shuffle(sample)
        null.append(_statistic(statistic, sample, annualization))
    valid = [v for v in null if v == v]
    if observed != observed or not valid:
        p = None
    else:
        hits = sum(1 for v in valid if abs(v) >= abs(observed))
        p = hits / len(valid)
    return {
        "scheme": scheme, "statistic": statistic, "n_permutations": n_permutations,
        "seed": int(seed), "observed": observed, "p_value": p,
        "null_summary": distribution_summary(valid),
        "note": "双侧 p（|null| ≥ |observed|）；只报数字不判定（ADR-13）",
    }


def noise_baseline(
    pool_returns: Sequence[float],
    *,
    n_hold: int,
    observed: float,
    n_draws: int = 1000,
    seed: int = 0,
    statistic: str = "sharpe",
    annualization: int = 252,
) -> dict[str, Any]:
    """随机策略对照（"噪声尺子"）：同**持有天数**随机择时分布。

    每次抽样从 `pool_returns`（同区间市场/基准日收益）中有放回抽 `n_hold` 天，
    计算统计量 ⇒ 分布；`observed_percentile` = 观测值在该分布中的分位
    （0.5 附近即"与随机择时无异"）。
    """
    if n_hold < 1:
        raise ValueError(f"n_hold 须 ≥ 1，得 {n_hold!r}")
    pool = _clean(pool_returns)
    if not pool:
        raise ValueError("noise_baseline: pool_returns 为空（不可算项不得伪造）")
    rng = random.Random(f"noise:{statistic}:{int(seed)}")
    draws: list[float] = []
    for _ in range(n_draws):
        sample = [pool[rng.randrange(len(pool))] for _ in range(n_hold)]
        draws.append(_statistic(statistic, sample, annualization))
    valid = [v for v in draws if v == v]
    summary = distribution_summary(valid)
    pct = (sum(1 for v in valid if v <= observed) / len(valid)
           if valid and observed == observed else None)
    return {
        "statistic": statistic, "n_hold": n_hold, "n_draws": n_draws,
        "seed": int(seed), "observed": observed,
        "observed_percentile": pct,
        "p05": summary["p05"], "p50": summary["p50"], "p95": summary["p95"],
        "n_valid": summary["n"],
    }
