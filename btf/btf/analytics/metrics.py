# -*- coding: utf-8 -*-
"""15 项核心绩效指标 + NAV 四式（M2 任务 5.4；04 §8.2.6 N5-1 / 11 号 v0.4）。

NAV 四式（04 §8.2.6 N5-1，快照字段唯一计算公式；参考计算器对账锚点）：
    total_value_t       = cash_t + Σ qty_i × close_名义_i  （引擎快照产出）
    daily_return_t      = tv_t / tv_{t−1} − 1              （首日 = 0）
    cumulative_return_t = tv_t / tv_0 − 1
    drawdown_t          = tv_t / max(tv_0..t) − 1

**加法禁区**（N5-1，H4 同族错误）：realized_pnl 已通过 avg_cost 摊薄吸收
分红，禁止「已实现收益 = realized_pnl + 分红现金」式相加——总收益一律以
**total_value（NAV）口径**为准。本模块全部收益类指标只消费 NAV 序列，
绝不读 realized_pnl / 分红现金（回归：tests/unit/test_analytics_metrics.py
TestAdditionForbidden）。

分层注记（03 §7.2）：analytics 位于 portfolio 之下——快照/成交以**鸭子类型**
消费（不 import portfolio / engine），契约由装配期注入的类型满足。

降级（04 §8.5 扩展点 8）：单指标计算异常（样本不足 / 除零 / 定义域）→
该指标置 NaN，不中断其余指标。

指标集（11 号 v0.4，15 项）：
    NAV 口径   final_nav / total_return / annualized_return /
               annualized_volatility / sharpe_ratio / max_drawdown /
               calmar_ratio / win_rate / profit_loss_ratio
    成交口径   n_fills / total_fees / total_turnover /
               turnover_annualized / fee_ratio
    元信息     n_trading_days

配置键（``config``，Analyzer 协议第三参）：
    annualization_factor  年化交易日数（默认 252，A 股）
    risk_free_rate        无风险利率（年化，默认 0.0；Sharpe 用，线性日化）
"""
from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from btf.domain.contracts import CONTRACT_VERSION

logger = logging.getLogger(__name__)

#: 降级值（04 §8.5 扩展点 8：指标异常不中断）
NAN = math.nan
#: 默认年化交易日（A 股）
DEFAULT_ANNUALIZATION = 252
#: 默认无风险利率（年化）
DEFAULT_RISK_FREE = 0.0

# ─────────────────────────────────────────────────────────────
# NAV 四式（04 §8.2.6 N5-1）——纯函数，参考计算器对账锚点
# ─────────────────────────────────────────────────────────────
def nav_total_values(snapshots: Sequence[Any]) -> list[float]:
    """第一式：NAV 序列（引擎快照 total_value = cash + Σ qty×close_名义）。"""
    return [float(s.total_value) for s in snapshots]


def nav_daily_returns(nav: Sequence[float]) -> list[float]:
    """第二式：r_t = tv_t / tv_{t−1} − 1（首日 = 0；前值 0 → 0 不除零）。"""
    out: list[float] = []
    for i, tv in enumerate(nav):
        prev = nav[i - 1] if i else 0.0
        out.append(0.0 if i == 0 or prev == 0 else tv / prev - 1.0)
    return out


def nav_cumulative_returns(nav: Sequence[float]) -> list[float]:
    """第三式：R_t = tv_t / tv_0 − 1（基准 tv_0 = 0 → 全 0）。"""
    if not nav:
        return []
    base = nav[0]
    return [0.0 if base == 0 else tv / base - 1.0 for tv in nav]


def nav_drawdowns(nav: Sequence[float]) -> list[float]:
    """第四式：dd_t = tv_t / max(tv_0..t) − 1（≤0；峰值 0 → 0）。"""
    out: list[float] = []
    peak = 0.0
    for tv in nav:
        peak = max(peak, tv)
        out.append(0.0 if peak == 0 else tv / peak - 1.0)
    return out


@dataclass(frozen=True)
class NavDrift:
    """NAV 四式对账漂移（快照字段 vs 本模块重算）。"""

    date: Any
    field: str
    snapshot_value: float
    recomputed: float


def audit_nav_four(snapshots: Sequence[Any], *, tol: float = 1e-9
                   ) -> list[NavDrift]:
    """NAV 四式对账：引擎快照字段 vs 四式重算，返回漂移明细（空=一致）。

    对账锚点用途：快照字段（引擎产出）与四式（参考计算器）双轨——口径
    双写漂移可被机检捕获（H4 教训：公式只允许一处真源，故此处为**只读
    对账**而非替代计算）。
    """
    nav = nav_total_values(snapshots)
    daily = nav_daily_returns(nav)
    cumulative = nav_cumulative_returns(nav)
    drawdowns = nav_drawdowns(nav)
    drifts: list[NavDrift] = []
    for i, snap in enumerate(snapshots):
        checks = (
            ("total_value", snap.total_value, snap.cash + snap.market_value),
            ("daily_return", snap.daily_return, daily[i]),
            ("cumulative_return", snap.cumulative_return, cumulative[i]),
            ("drawdown", snap.drawdown, drawdowns[i]),
        )
        for field, got, expected in checks:
            if abs(float(got) - float(expected)) > tol:
                drifts.append(NavDrift(getattr(snap, "date", i), field,
                                       float(got), float(expected)))
    return drifts


# ─────────────────────────────────────────────────────────────
# 指标计算基座
# ─────────────────────────────────────────────────────────────
def _fee_total(fill: Any) -> float:
    """成交费用总额（鸭子类型读 fee.total；缺失 → 0）。"""
    fee = getattr(fill, "fee", None)
    return float(getattr(fee, "total", 0.0) or 0.0)


def _fill_amount(fill: Any) -> float:
    """成交金额（双边口径：买入/卖出均计全额 price×qty）。"""
    return float(getattr(fill, "price", 0.0)) * float(getattr(fill, "qty", 0) or 0)


@dataclass(frozen=True)
class NavSeries:
    """指标基座：NAV 四式结果 + 成交汇总（纯函数产物，15 指标共享）。"""

    nav: tuple[float, ...]
    daily_returns: tuple[float, ...]
    cumulative_returns: tuple[float, ...]
    drawdowns: tuple[float, ...]
    fees: float = 0.0            # Σ fee.total（元）
    turnover: float = 0.0        # Σ price×qty（元，双边）
    n_fills: int = 0
    #: **R26′（19 号 §96.4；N31）**：在场日掩码（与快照对齐；空 = 无信息 ⇒
    #: 口径回落全样本——向后兼容）。在场判定＝该日快照 `positions_qty` 非空。
    in_market: tuple[bool, ...] = ()
    #: **R26′**：FIFO 开平配对交易（`analytics.trades.pair_fills_to_trades`；
    #: 逐笔口径指标消费；空 = 未传入成交）。
    trades: tuple[Any, ...] = ()

    @property
    def n_days(self) -> int:
        """快照日数（= 回测区间交易日数）。"""
        return len(self.nav)

    @property
    def initial_nav(self) -> float:
        """tv_0（空 → NaN）。"""
        return self.nav[0] if self.nav else NAN

    @property
    def final_nav(self) -> float:
        """tv_T（空 → NaN）。"""
        return self.nav[-1] if self.nav else NAN

    def mean_nav(self) -> float:
        """平均 NAV（换手/费用占比的分母；空 → NaN）。"""
        return statistics.fmean(self.nav) if self.nav else NAN

    def effective_returns(self) -> tuple[float, ...]:
        """有效日收益（排除首日——首日 daily_return=0 非真实收益）。"""
        return self.daily_returns[1:] if self.n_days > 1 else ()

    def in_market_returns(self) -> tuple[float, ...]:
        """**在场日**日收益序列（R26′/N31：排除空仓日；无掩码 ⇒ 回落全样本）。

        在场判定＝快照当日 `positions_qty` 非空（持仓日）；首日仍排除
        （daily_return=0 非真实收益）。全时段持仓 ⇒ 与 `effective_returns()`
        **逐位相同**（v77 等满仓 run 的 `metrics_digest` 因此零变更）。
        """
        if not self.in_market or len(self.in_market) != self.n_days:
            return self.effective_returns()
        return tuple(r for r, m in zip(self.daily_returns[1:],
                                       self.in_market[1:], strict=True) if m)

    def total_return(self) -> float:
        """累计收益 = 第三式期末值（NAV 口径，空 → IndexError 降级）。"""
        return self.cumulative_returns[-1]


def build_nav_series(snapshots: Sequence[Any], trades: Sequence[Any] = (), *,
                     consolidations: Sequence[Any] | None = None) -> NavSeries:
    """快照 + 成交 → 指标基座（NAV 四式 + 成交汇总；纯函数）。

    **R26′（19 号 §96.4；N31）**：另派生
    ① **在场日掩码**（快照 `positions_qty` 非空——`win_rate` 分母口径）；
    ② **FIFO 开平配对交易**（逐笔口径辅助指标 `win_rate_trades` 消费）。

    **`consolidations`（R61′/N63；19 号 §114.2）**：份额折算事件序列——
    配对须感知折算（否则未平仓虚高、逐笔胜率失真）；缺省 `None`＝旧行为
    （无折算信息的调用方**逐位不变**）。
    """
    nav = nav_total_values(snapshots)
    fills = list(trades or ())
    in_market = tuple(
        bool(getattr(s, "positions_qty", None)) for s in snapshots)
    if not any(in_market):
        # 全部快照无持仓 **或** 快照对象未携带 `positions_qty`——两者不可区分 ⇒
        # 保守回落「全样本」口径（`in_market_returns` 回落分支；向后兼容：
        # 老快照/自定义对象的 `win_rate` 取值逐位不变）。
        in_market = ()
    paired: tuple[Any, ...] = ()
    if fills:
        from btf.analytics.trades import pair_fills_to_trades

        paired = tuple(pair_fills_to_trades(fills, consolidations=consolidations))
    return NavSeries(
        nav=tuple(nav),
        daily_returns=tuple(nav_daily_returns(nav)),
        cumulative_returns=tuple(nav_cumulative_returns(nav)),
        drawdowns=tuple(nav_drawdowns(nav)),
        fees=sum(_fee_total(f) for f in fills),
        turnover=sum(_fill_amount(f) for f in fills),
        n_fills=len(fills),
        in_market=in_market,
        trades=paired,
    )


def _annualization_factor(config: Mapping[str, Any]) -> float:
    factor = float(config.get("annualization_factor", DEFAULT_ANNUALIZATION))
    if factor <= 0:
        raise ValueError(f"annualization_factor 须 >0，得 {factor}")
    return factor


def _risk_free_rate(config: Mapping[str, Any]) -> float:
    return float(config.get("risk_free_rate", DEFAULT_RISK_FREE))


def _periods(series: NavSeries) -> int:
    """年化基数 = 收益日数（快照数 − 1；<2 → 不可年化）。"""
    if series.n_days < 2:
        raise ValueError("交易日不足（<2），无法年化")
    return series.n_days - 1


def _total_return(series: NavSeries) -> float:
    """累计收益率（NAV 口径，四式第三式期末值）。"""
    return series.total_return()


def _annualized_return(series: NavSeries, config: Mapping[str, Any]) -> float:
    total = _total_return(series)
    base = 1.0 + total
    if base <= 0:
        raise ValueError(f"累计收益 {total} 使 1+R ≤ 0，年化无实数解")
    return base ** (_annualization_factor(config) / _periods(series)) - 1.0


# ─────────────────────────────────────────────────────────────
# 15 项指标（04 §8.3.6 Analyzer 协议：name + compute）
# ─────────────────────────────────────────────────────────────
class MetricBase:
    """指标基类：compute 包裹 _value，异常 → NaN（04 §8.5 扩展点 8）。"""

    contract_version: ClassVar[str] = CONTRACT_VERSION
    name: ClassVar[str] = ""

    def compute(self, snapshots: Sequence[Any], trades: Sequence[Any],
                config: Mapping[str, Any]) -> float:
        """04 §8.3.6 协议实现（纯函数：快照序列 + 成交序列 + 配置 → 标量）。"""
        try:
            return float(self._value(build_nav_series(snapshots, trades),
                                     config or {}))
        except (ZeroDivisionError, ValueError, TypeError, IndexError,
                statistics.StatisticsError) as exc:
            logger.debug("指标 %s 降级 → NaN（%s）", self.name, exc)
            return NAN

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        raise NotImplementedError


class FinalNav(MetricBase):
    """期末 NAV（元）= tv_T（四式第一式序列末值）。"""

    name = "final_nav"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        return series.final_nav


class TotalReturn(MetricBase):
    """累计收益率 = tv_T / tv_0 − 1（四式第三式期末值；NAV 口径）。

    **加法禁区**：不得由 realized_pnl + 分红现金求得（N5-1）。
    """

    name = "total_return"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        return _total_return(series)


class AnnualizedReturn(MetricBase):
    """年化收益率 = (1 + R)^(annualization_factor / 收益日数) − 1。"""

    name = "annualized_return"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        return _annualized_return(series, config)


class AnnualizedVolatility(MetricBase):
    """年化波动率 = stdev(有效日收益) × √annualization_factor（样本标准差）。"""

    name = "annualized_volatility"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        eff = series.effective_returns()
        if len(eff) < 2:
            raise ValueError("有效收益样本 < 2，波动率无定义")
        return statistics.stdev(eff) * math.sqrt(_annualization_factor(config))


class SharpeRatio(MetricBase):
    """夏普比率 = (mean(r) − rf/年化因子) / stdev(r) × √年化因子。

    rf 为**年化**无风险利率，按线性日化（rf / 年化因子）简化处理。
    """

    name = "sharpe_ratio"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        eff = series.effective_returns()
        if len(eff) < 2:
            raise ValueError("有效收益样本 < 2，夏普无定义")
        factor = _annualization_factor(config)
        sd = statistics.stdev(eff)
        if sd == 0:
            raise ZeroDivisionError("日收益波动为 0，夏普无定义")
        excess = statistics.fmean(eff) - _risk_free_rate(config) / factor
        return excess / sd * math.sqrt(factor)


class SharpeStderr(MetricBase):
    """夏普**标准误**（**RC-06，2026-10-05：可选指标——不进默认 15 项**）。

    口径：`analytics.inference.sharpe_stderr`（B&LdP 方差式，含偏度/峰度修正）——
    入参为**单期**夏普。样本 < 3 或方差为 0 时抛错 ⇒ 由 `compute_all` 统一降级 NaN。

    用途：把"夏普的点估计"变成"点估计 ± 不确定性"；须在
    `analysis.metrics` 中**显式声明**才计算（默认指标集不变）。
    """

    name = "sharpe_stderr"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        from btf.analytics.inference import sharpe_moments, sharpe_stderr

        n, sr_period, skew, kurt = sharpe_moments(series.effective_returns())
        return sharpe_stderr(sr_period, n_periods=n, skew=skew, kurt=kurt)


class MaxDrawdown(MetricBase):
    """最大回撤 = min(dd_t)（四式第四式序列最小值，≤0）。"""

    name = "max_drawdown"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        return min(series.drawdowns)


class CalmarRatio(MetricBase):
    """卡玛比率 = 年化收益率 / |最大回撤|（回撤为 0 → 降级 NaN）。"""

    name = "calmar_ratio"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        mdd = abs(min(series.drawdowns))
        if mdd == 0:
            raise ZeroDivisionError("最大回撤为 0，卡玛无定义")
        return _annualized_return(series, config) / mdd


class WinRate(MetricBase):
    """胜率 = 盈利日数 / **在场日**数（R26′/N31；排除首日；平盘日计入分母）。

    **口径变更（19 号 §96.4 R26′；DOC-11）**：分母**禁含空仓日**——原实现
    为「盈利日 ÷ 全部快照日」（510880 实测 26.93%＝把择时策略胜率系统性稀释；
    在场日真实胜率 ≈52.8%）。全时段持仓 run（如 v77 股票轮动）在场日＝全部日
    ⇒ 取值与原口径**逐位相同**（`metrics_digest` 零变更）。报告须标注所用口径
    （R26′；20 号纪律四）；逐笔口径＝`win_rate_trades`（可选指标，辅助并列）。
    """

    name = "win_rate"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        eff = series.in_market_returns()
        if not eff:
            raise ValueError("无有效收益日，胜率无定义")
        return sum(1 for r in eff if r > 0) / len(eff)


class WinRateTrades(MetricBase):
    """**逐笔口径胜率（R26′ 辅助指标）**：盈利平仓笔数 ÷ 平仓笔数。

    平仓判定＝FIFO 配对 `Trade.close_fill is not None`（未平仓不入分母）；
    平盘（pnl=0）计入分母不计盈利。须在 `analysis.metrics` **显式声明**
    （**可选指标**——不进默认 15 项，护 `metrics_digest` 口径不变）。
    """

    name = "win_rate_trades"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        closed = [t for t in series.trades
                  if getattr(t, "close_fill", None) is not None]
        if not closed:
            raise ValueError("无平仓交易，逐笔胜率无定义")
        return (sum(1 for t in closed if float(getattr(t, "pnl", 0.0)) > 0.0)
                / len(closed))


class ProfitLossRatio(MetricBase):
    """盈亏比 = 平均盈利日收益 / |平均亏损日收益|（缺一侧 → 降级 NaN）。"""

    name = "profit_loss_ratio"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        eff = series.effective_returns()
        wins = [r for r in eff if r > 0]
        losses = [r for r in eff if r < 0]
        if not wins or not losses:
            raise ValueError("缺盈利日或亏损日，盈亏比无定义")
        return statistics.fmean(wins) / abs(statistics.fmean(losses))


class NFills(MetricBase):
    """成交笔数（Fill 条数）。"""

    name = "n_fills"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        return float(series.n_fills)


class TotalFees(MetricBase):
    """总费用（元）= Σ fee.total（佣金+印花税+过户费）。"""

    name = "total_fees"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        return series.fees


class TotalTurnover(MetricBase):
    """总成交额（元）= Σ price×qty（双边：买卖各计全额）。"""

    name = "total_turnover"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        return series.turnover


class TurnoverAnnualized(MetricBase):
    """年化双边换手率 = 总成交额 / 平均 NAV × (年化因子 / 收益日数)。"""

    name = "turnover_annualized"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        mean_nav = series.mean_nav()
        if mean_nav == 0:
            raise ZeroDivisionError("平均 NAV 为 0，换手率无定义")
        return (series.turnover / mean_nav
                * (_annualization_factor(config) / _periods(series)))


class FeeRatio(MetricBase):
    """费用占比 = 总费用 / 平均 NAV（累计费用相对平均资产的拖累）。"""

    name = "fee_ratio"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        mean_nav = series.mean_nav()
        if mean_nav == 0:
            raise ZeroDivisionError("平均 NAV 为 0，费用占比无定义")
        return series.fees / mean_nav


class NTradingDays(MetricBase):
    """交易日数（快照条数）。"""

    name = "n_trading_days"

    def _value(self, series: NavSeries, config: Mapping[str, Any]) -> float:
        return float(series.n_days)


#: 15 项指标类全集（registry ANALYZER 扩展点名字表来源）
METRIC_CLASSES: tuple[type[MetricBase], ...] = (
    FinalNav, TotalReturn, AnnualizedReturn, AnnualizedVolatility,
    SharpeRatio, MaxDrawdown, CalmarRatio, WinRate, ProfitLossRatio,
    NFills, TotalFees, TotalTurnover, TurnoverAnnualized, FeeRatio,
    NTradingDays,
)

#: **可选指标**（RC-06，2026-10-05）：须在 `analysis.metrics` **显式声明**才计算，
#: **不进默认 15 项**（默认指标集与 `metrics_digest` 口径因此保持不变）。
#: `WinRateTrades`（R26′，2026-10-07）：逐笔口径胜率辅助指标（N31 修复配套）。
OPTIONAL_METRIC_CLASSES: tuple[type[MetricBase], ...] = (SharpeStderr,
                                                         WinRateTrades)

#: 指标名 → 类（配置声明制：用户侧只写名字；含可选指标）
METRICS: Mapping[str, type[MetricBase]] = {
    cls.name: cls for cls in (*METRIC_CLASSES, *OPTIONAL_METRIC_CLASSES)}

#: 15 项**默认**指标名（有序=上表顺序；可选指标不在此列）
METRIC_NAMES: tuple[str, ...] = tuple(cls.name for cls in METRIC_CLASSES)


def compute_all(
    snapshots: Sequence[Any], trades: Sequence[Any] = (),
    config: Mapping[str, Any] | None = None, *,
    analyzer_names: Sequence[str] | None = None,
    resolver: Callable[[str], type[MetricBase]] | None = None,
    consolidations: Sequence[Any] | None = None,
) -> dict[str, float]:
    """一次性算指标集（报告/落盘用；单指标失败只该项为 NaN）。

    `analyzer_names` + `resolver`（19 号审查 P1-1/EX-1）：**扩展点接线**——
    传入名字表解析器（如 `registry.resolve(ANALYZER, …)`，由装配层
    `runtime` 注入，避免 analytics→registry 的分层违规）后，经
    `registry.register()` 注册的自定义 Analyzer **零源码改动即生效**；
    缺省（None）保持"内置全集"语义不变（RK-6 缓解）。

    `consolidations`（R61′/N63，§114.2）：份额折算序列，透传 `build_nav_series`
    ⇒ FIFO 配对感知折算（只影响逐笔口径指标 `win_rate_trades` 与 `trades`
    明细；**NAV 口径 15 项逐位不变**）。
    """
    cfg = config or {}
    # 缺省 = **默认 15 项**（`METRIC_NAMES`）——不含 `OPTIONAL_METRIC_CLASSES`
    # （RC-06：可选指标须显式声明；默认集与 digest 口径不变）
    names = (list(analyzer_names) if analyzer_names is not None
             else list(METRIC_NAMES))
    resolve = resolver or (lambda name: METRICS[name])
    series = build_nav_series(snapshots, trades, consolidations=consolidations)
    out: dict[str, float] = {}
    for name in names:
        metric = resolve(name)()
        try:
            out[metric.name] = float(metric._value(series, cfg))
        except (ZeroDivisionError, ValueError, TypeError, IndexError,
                statistics.StatisticsError) as exc:
            logger.debug("指标 %s 降级 → NaN（%s）", metric.name, exc)
            out[metric.name] = NAN
    return out


__all__ = [
    "DEFAULT_ANNUALIZATION",
    "DEFAULT_RISK_FREE",
    "METRICS",
    "METRIC_CLASSES",
    "METRIC_NAMES",
    "NAN",
    "OPTIONAL_METRIC_CLASSES",
    "AnnualizedReturn",
    "AnnualizedVolatility",
    "CalmarRatio",
    "FeeRatio",
    "FinalNav",
    "MaxDrawdown",
    "MetricBase",
    "NFills",
    "NTradingDays",
    "NavDrift",
    "NavSeries",
    "ProfitLossRatio",
    "SharpeRatio",
    "SharpeStderr",
    "TotalFees",
    "TotalReturn",
    "TotalTurnover",
    "TurnoverAnnualized",
    "WinRate",
    "audit_nav_four",
    "build_nav_series",
    "compute_all",
    "nav_cumulative_returns",
    "nav_daily_returns",
    "nav_drawdowns",
    "nav_total_values",
]
