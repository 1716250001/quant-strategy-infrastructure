# -*- coding: utf-8 -*-
"""基准相对指标（19 号架构审查 P1-10 / §C.3.1；R2.5）。**纯函数，零数据依赖**。

**要解决的问题**：`report.benchmark` 是死键（schema 有、样例配置有、
`btf/` 零消费）——用户写了校验通过但不生效、不报错、报告无痕迹；
15 项指标无任何相对指标；六图无基准曲线。而 `index_daily` 数据现成。

分工（分层契约）：本模块只做**计算**；基准取数在 `data/index_series.py`
（`analytics` 与 `data` 同层互禁），由 `runtime` 装配层编排。

口径：
    - 策略净值与基准序列**同日对齐**（长度必须相等）；
    - 两侧都归一化到首日 = 1.0（避免"起点错一天"的经典错误）；
    - 相对指标 ≥6 项：超额/年化超额/跟踪误差/信息比率/beta/alpha/
      上行捕获/下行捕获/基准总收益。
"""
from __future__ import annotations

from collections.abc import Sequence
from math import sqrt

__all__ = ["BenchmarkError", "relative_metrics"]


class BenchmarkError(RuntimeError):
    """相对指标计算失败（口径不满足：长度不等/序列过短等）。"""


def relative_metrics(
    strategy_navs: Sequence[float],
    benchmark_closes: Sequence[float],
    *,
    symbol: str = "",
    risk_free_rate: float = 0.0,
    annualization_factor: int = 252,
) -> dict[str, float]:
    """相对指标集（同日对齐；两侧归一到首日 = 1.0）。"""
    if len(strategy_navs) != len(benchmark_closes):
        raise BenchmarkError(
            f"策略净值长度 {len(strategy_navs)} ≠ 基准 {len(benchmark_closes)}")
    n = len(strategy_navs)
    if n < 3:
        # 样本方差/协方差/跟踪误差均除以 (n−1) 的自由度 → n=2 时除零。
        # 原守卫写 `< 2`，2 日序列会抛 **ZeroDivisionError**（非文档口径的
        # BenchmarkError）——Z-2 补测时实测踩到，按契约改为 fail-closed。
        raise BenchmarkError(
            f"序列过短（{n} 日 < 3）无法计算相对指标："
            f"样本方差与跟踪误差需 n−1 ≥ 2 个收益样本")
    base_s, base_b = strategy_navs[0], benchmark_closes[0]
    if not base_s > 0 or not base_b > 0:
        raise BenchmarkError("首日净值/收盘 ≤ 0，无法归一化")
    navs = [v / base_s for v in strategy_navs]
    bench = [v / base_b for v in benchmark_closes]
    ann = float(annualization_factor)
    s_rets = [navs[i] / navs[i - 1] - 1.0 for i in range(1, n)]
    b_rets = [bench[i] / bench[i - 1] - 1.0 for i in range(1, n)]
    mean_s = sum(s_rets) / len(s_rets)
    mean_b = sum(b_rets) / len(b_rets)
    rf_daily = (1.0 + risk_free_rate) ** (1.0 / ann) - 1.0
    var_b = sum((r - mean_b) ** 2 for r in b_rets) / (len(b_rets) - 1)
    var_s = sum((r - mean_s) ** 2 for r in s_rets) / (len(s_rets) - 1)
    cov = sum((s - mean_s) * (b - mean_b)
              for s, b in zip(s_rets, b_rets, strict=True)) / (len(s_rets) - 1)
    beta = cov / var_b if var_b > 0 else float("nan")
    alpha_daily = (mean_s - rf_daily) - beta * (mean_b - rf_daily)
    tracking = [s - b for s, b in zip(s_rets, b_rets, strict=True)]
    mean_track = sum(tracking) / len(tracking)
    te_daily = sqrt(sum((t - mean_track) ** 2 for t in tracking)
                    / (len(tracking) - 1))
    up = [(s, b) for s, b in zip(s_rets, b_rets, strict=True) if b > 0]
    down = [(s, b) for s, b in zip(s_rets, b_rets, strict=True) if b < 0]

    def _capture(pairs: list[tuple[float, float]]) -> float:
        if not pairs:
            return float("nan")
        mean_bp = sum(b for _s, b in pairs) / len(pairs)
        mean_sp = sum(s for s, _b in pairs) / len(pairs)
        return mean_sp / mean_bp if mean_bp != 0 else float("nan")

    return {
        "benchmark_symbol": symbol,                       # type: ignore[dict-item]
        "benchmark_total_return": bench[-1] / bench[0] - 1.0,
        "excess_return": navs[-1] - bench[-1],
        "annualized_excess": (1.0 + mean_s - mean_b) ** ann - 1.0,
        "tracking_error": te_daily * sqrt(ann),
        "information_ratio": (mean_track / te_daily * sqrt(ann)
                              if te_daily > 0 else float("nan")),
        "beta": beta,
        "alpha_annualized": alpha_daily * ann,
        "up_capture": _capture(up),
        "down_capture": _capture(down),
        "strategy_volatility_annualized": sqrt(var_s) * sqrt(ann),
    }
