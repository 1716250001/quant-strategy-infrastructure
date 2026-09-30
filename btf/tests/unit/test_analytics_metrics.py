# -*- coding: utf-8 -*-
"""绩效指标单测（M2 任务 5.4；04 §8.2.6 N5-1 NAV 四式 + 11 号 v0.4 15 项）。

覆盖（18 号 5.4 验收）：
    1. NAV 四式纯函数（逐条对照公式）+ 与引擎快照字段**对账**（audit_nav_four）；
    2. 15 项指标数值（期望值以显式统计公式独立复算，不调用被测实现）；
    3. 降级：样本不足/除零 → NaN 不中断（04 §8.5 扩展点 8）；
    4. **加法禁区回归（N5）**：realized_pnl + 分红现金 ≠ 总收益——
       总收益一律 NAV 口径，指标不消费 realized_pnl；
    5. registry ANALYZER 扩展点：15 名可解析 + 版本协商 + 未知名报错。
"""
from __future__ import annotations

import math
from dataclasses import FrozenInstanceError, dataclass

import pytest
from btf.analytics.metrics import (
    METRIC_NAMES,
    NAN,
    AnnualizedReturn,
    AnnualizedVolatility,
    CalmarRatio,
    FeeRatio,
    FinalNav,
    MaxDrawdown,
    MetricBase,
    NavSeries,
    NFills,
    NTradingDays,
    ProfitLossRatio,
    SharpeRatio,
    TotalFees,
    TotalReturn,
    TotalTurnover,
    TurnoverAnnualized,
    WinRate,
    audit_nav_four,
    build_nav_series,
    compute_all,
    nav_cumulative_returns,
    nav_daily_returns,
    nav_drawdowns,
    nav_total_values,
)
from btf.domain.action import CorporateAction
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.orders import Fee, Fill, OrderSide
from btf.domain.types import TradingDate
from btf.portfolio.portfolio import Portfolio, PortfolioSnapshot
from btf.registry import ANALYZER, RegistryError, available, create

pytestmark = [pytest.mark.l1]

D = TradingDate.from_ymd
SYM = "000001.SZ"
#: 收盘价序列 → NAV = [10000, 12000, 9000, 10000]（持仓 1000 股，现金 0）
PRICES = [10.0, 12.0, 9.0, 10.0]
NAV = (10_000.0, 12_000.0, 9_000.0, 10_000.0)
#: 有效日收益（手算：12000/10000−1；9000/12000−1；10000/9000−1）
EFF = (0.2, -0.25, 1.0 / 9.0)
#: 年化因子（测试用 3 → 年化指数 = 因子/收益日数 = 1，便于手算）
FACTOR = 3


def mk_fill(side: OrderSide, qty: int, price: float, fee_total: float,
            fid: str = "F1") -> Fill:
    return Fill(fill_id=fid, order_id="O1", symbol=SYM, side=side, qty=qty,
                price=price,
                fee=Fee(commission=fee_total, stamp_duty=0.0,
                        transfer_fee=0.0, total=fee_total),
                fill_date=D("20150106"), fill_timing="open")


def _snapshots(prices: list[float] | None = None) -> list[PortfolioSnapshot]:
    """Portfolio 真实路径产出快照（引擎同款 NAV 四式；对账的另一轨）。"""
    prices = PRICES if prices is None else prices
    pf = Portfolio(cash=10_000.0)
    pf.apply_fill(mk_fill(OrderSide.BUY, 1_000, prices[0], 0.0))   # 建仓 1000 股
    out = []
    for i, price in enumerate(prices):
        pf.mark_all({SYM: price})
        out.append(pf.snapshot(D(f"2015010{5 + i}")))
    return out


def _trades() -> list[Fill]:
    """两笔成交：买 1000@10 费 5；卖 1000@10 费 6（与快照解耦，仅成交口径）。"""
    return [mk_fill(OrderSide.BUY, 1_000, 10.0, 5.0, "F1"),
            mk_fill(OrderSide.SELL, 1_000, 10.0, 6.0, "F2")]


def _cfg(**kw) -> dict:
    return {"annualization_factor": FACTOR, **kw}


def _ref_vol(eff=EFF, factor: float = FACTOR) -> float:
    """年化波动参考值（显式公式独立复算：样本标准差 × √因子）。"""
    mean = sum(eff) / len(eff)
    var = sum((r - mean) ** 2 for r in eff) / (len(eff) - 1)
    return math.sqrt(var) * math.sqrt(factor)


def _ref_sharpe(eff=EFF, factor: float = FACTOR, rf: float = 0.0) -> float:
    """夏普参考值（显式公式独立复算）。"""
    mean = sum(eff) / len(eff)
    var = sum((r - mean) ** 2 for r in eff) / (len(eff) - 1)
    return (mean - rf / factor) / math.sqrt(var) * math.sqrt(factor)


# ═════════════════════════════════════════════════════════════
# NAV 四式
# ═════════════════════════════════════════════════════════════
class TestNavFour:
    """04 §8.2.6 N5-1 四式：逐条对照 + 与引擎快照字段对账。"""

    def test_first_form_total_values(self):
        """第一式：NAV 序列 = 快照 total_value（引擎产出）。"""
        assert nav_total_values(_snapshots()) == pytest.approx(list(NAV))

    def test_second_form_daily_returns(self):
        """第二式：r_t = tv_t/tv_{t−1} − 1；首日 = 0。"""
        got = nav_daily_returns(NAV)
        assert got[0] == 0.0
        assert got[1:] == pytest.approx(list(EFF))

    def test_second_form_prev_zero_no_div_error(self):
        """前值为 0（极端）→ 返回 0 而非除零崩溃（降级不中断）。"""
        assert nav_daily_returns([0.0, 100.0]) == [0.0, 0.0]

    def test_third_form_cumulative(self):
        """第三式：R_t = tv_t/tv_0 − 1。"""
        assert nav_cumulative_returns(NAV) == pytest.approx(
            [0.0, 0.2, -0.1, 0.0])

    def test_fourth_form_drawdown(self):
        """第四式：dd_t = tv_t/max(tv_0..t) − 1（峰值单调，≤0）。"""
        got = nav_drawdowns(NAV)
        assert got == pytest.approx([0.0, 0.0, -0.25, -1.0 / 6.0])
        assert max(got) <= 0.0

    def test_empty_inputs(self):
        assert nav_daily_returns([]) == []
        assert nav_cumulative_returns([]) == []
        assert nav_drawdowns([]) == []

    def test_audit_matches_engine_snapshots(self):
        """对账：引擎快照字段 vs 四式重算 → 无漂移（双轨口径一致）。"""
        snaps = _snapshots()
        assert audit_nav_four(snaps) == []

    def test_audit_detects_field_drift(self):
        """反例：篡改快照 daily_return → 对账报漂移（机检有效）。"""
        snaps = _snapshots()
        tampered = [PortfolioSnapshot(**{**vars(s), "daily_return": 0.99})
                    if i == 2 else s for i, s in enumerate(snaps)]
        drifts = audit_nav_four(tampered)
        assert [d.field for d in drifts] == ["daily_return"]
        assert drifts[0].recomputed == pytest.approx(-0.25)

    def test_audit_detects_total_value_sum_drift(self):
        """反例：total_value ≠ cash + market_value → 第一式漂移。"""
        snap = _snapshots()[0]
        bad = PortfolioSnapshot(**{**vars(snap), "total_value": 12_345.0})
        assert [d.field for d in audit_nav_four([bad])] == ["total_value"]


# ═════════════════════════════════════════════════════════════
# 指标基座
# ═════════════════════════════════════════════════════════════
class TestNavSeries:
    """指标基座：四式结果 + 成交汇总。"""

    def test_build_series(self):
        s = build_nav_series(_snapshots(), _trades())
        assert s.nav == pytest.approx(NAV)
        assert s.n_days == 4
        assert s.initial_nav == pytest.approx(10_000.0)
        assert s.final_nav == pytest.approx(10_000.0)
        assert s.mean_nav() == pytest.approx(10_250.0)      # (10+12+9+10)k/4
        assert s.fees == pytest.approx(11.0)
        assert s.turnover == pytest.approx(20_000.0)        # 双边：10000+10000
        assert s.n_fills == 2

    def test_effective_returns_excludes_first_day(self):
        s = build_nav_series(_snapshots())
        assert s.effective_returns() == pytest.approx(EFF)

    def test_empty_series(self):
        s = build_nav_series([])
        assert math.isnan(s.initial_nav) and math.isnan(s.final_nav)
        assert math.isnan(s.mean_nav())
        assert s.effective_returns() == ()

    def test_duck_typed_snapshots(self):
        """鸭子类型契约：analytics 不 import portfolio——任意同字段对象可用。"""

        @dataclass
        class FakeSnap:
            date: str
            cash: float
            market_value: float
            total_value: float
            daily_return: float
            cumulative_return: float
            drawdown: float
            realized_pnl: float = 999.0      # 干扰字段（禁区：不被消费）

        fake = [FakeSnap(f"d{i}", 0.0, tv, tv, 0.0, 0.0, 0.0)
                for i, tv in enumerate(NAV)]
        assert nav_total_values(fake) == pytest.approx(NAV)
        real = build_nav_series(_snapshots())
        duck = build_nav_series(fake)
        assert duck.cumulative_returns == pytest.approx(real.cumulative_returns)


# ═════════════════════════════════════════════════════════════
# 15 项指标
# ═════════════════════════════════════════════════════════════
class TestNavMetrics:
    """NAV 口径 9 项（期望值：手算 / 显式统计公式独立复算）。"""

    def test_final_nav(self):
        assert FinalNav().compute(_snapshots(), (), {}) == pytest.approx(10_000.0)

    def test_total_return(self):
        """总收益 = 第三式期末值 = 10000/10000 − 1 = 0（NAV 口径）。"""
        assert TotalReturn().compute(_snapshots(), (), {}) == pytest.approx(0.0)

    def test_annualized_return(self):
        """(1+0)^(3/3) − 1 = 0（因子 3 / 收益日 3）。"""
        assert AnnualizedReturn().compute(
            _snapshots(), (), _cfg()) == pytest.approx(0.0)

    def test_annualized_return_compounds(self):
        """NAV 翻倍（4 收益日，因子 4）→ 年化 = 2^1 − 1 = 1.0。"""
        snaps = _snapshots([10.0, 11.0, 12.0, 13.0, 20.0])
        assert AnnualizedReturn().compute(
            snaps, (), {"annualization_factor": 4}) == pytest.approx(1.0)

    def test_annualized_volatility(self):
        got = AnnualizedVolatility().compute(_snapshots(), (), _cfg())
        assert got == pytest.approx(_ref_vol())

    def test_sharpe_ratio(self):
        got = SharpeRatio().compute(_snapshots(), (), _cfg())
        assert got == pytest.approx(_ref_sharpe())

    def test_sharpe_with_risk_free(self):
        """rf=0.03 年化 → 日化 0.03/3，分子下移。"""
        got = SharpeRatio().compute(_snapshots(), (), _cfg(risk_free_rate=0.03))
        assert got == pytest.approx(_ref_sharpe(rf=0.03))

    def test_max_drawdown(self):
        assert MaxDrawdown().compute(
            _snapshots(), (), {}) == pytest.approx(-0.25)

    def test_calmar_ratio(self):
        """年化 0 / |−0.25| = 0。"""
        assert CalmarRatio().compute(_snapshots(), (), _cfg()) == pytest.approx(0.0)

    def test_calmar_ratio_nonzero(self):
        """NAV 10k→20k（4 收益日，因子 4）：年化 1.0；无回撤前另见降级例。"""
        snaps = _snapshots([10.0, 12.0, 9.0, 20.0])
        got = CalmarRatio().compute(snaps, (), {"annualization_factor": 4})
        # 年化 = (1+1)^(4/3) − 1；回撤 = 9000/12000 − 1 = −0.25
        expect = ((2.0) ** (4 / 3) - 1.0) / 0.25
        assert got == pytest.approx(expect)

    def test_win_rate(self):
        """盈利日 2 / 有效日 3。"""
        assert WinRate().compute(_snapshots(), (), {}) == pytest.approx(2 / 3)

    def test_win_rate_counts_flat_day_in_denominator(self):
        """平盘日计入分母（非盈利）：NAV 持平 → 胜率 1/3。"""
        snaps = _snapshots([10.0, 11.0, 11.0, 12.0])
        assert WinRate().compute(snaps, (), {}) == pytest.approx(2 / 3)

    def test_profit_loss_ratio(self):
        """mean(0.2, 1/9) / |mean(−0.25)|。"""
        expect = ((0.2 + 1 / 9) / 2) / 0.25
        assert ProfitLossRatio().compute(
            _snapshots(), (), {}) == pytest.approx(expect)


class TestTradeMetrics:
    """成交口径 5 项。"""

    def test_n_fills(self):
        assert NFills().compute(_snapshots(), _trades(), {}) == 2.0

    def test_n_fills_without_trades(self):
        assert NFills().compute(_snapshots(), (), {}) == 0.0

    def test_total_fees(self):
        assert TotalFees().compute(_snapshots(), _trades(), {}) == pytest.approx(11.0)

    def test_total_turnover(self):
        """双边：1000×10 + 1000×10 = 20000。"""
        assert TotalTurnover().compute(
            _snapshots(), _trades(), {}) == pytest.approx(20_000.0)

    def test_turnover_annualized(self):
        """20000 / 平均NAV 10250 × (3/3) = 1.9512…"""
        expect = 20_000.0 / 10_250.0
        assert TurnoverAnnualized().compute(
            _snapshots(), _trades(), _cfg()) == pytest.approx(expect)

    def test_turnover_annualized_scales_with_factor(self):
        """因子 252 / 收益日 3 → 84 倍放大。"""
        expect = 20_000.0 / 10_250.0 * (252 / 3)
        assert TurnoverAnnualized().compute(
            _snapshots(), _trades(),
            {"annualization_factor": 252}) == pytest.approx(expect)

    def test_fee_ratio(self):
        """11 / 10250。"""
        assert FeeRatio().compute(
            _snapshots(), _trades(), {}) == pytest.approx(11.0 / 10_250.0)

    def test_n_trading_days(self):
        assert NTradingDays().compute(_snapshots(), (), {}) == 4.0


# ═════════════════════════════════════════════════════════════
# 降级（04 §8.5 扩展点 8）
# ═════════════════════════════════════════════════════════════
class TestDegradation:
    """样本不足/除零 → 该指标 NaN，不抛异常。"""

    def test_empty_snapshots_all_nan_but_counts(self):
        for cls in (FinalNav, TotalReturn, AnnualizedReturn,
                    AnnualizedVolatility, SharpeRatio, MaxDrawdown,
                    CalmarRatio, WinRate, ProfitLossRatio, TurnoverAnnualized,
                    FeeRatio):
            assert math.isnan(cls().compute([], (), {})), cls.name
        assert NFills().compute([], (), {}) == 0.0
        assert TotalFees().compute([], (), {}) == 0.0
        assert NTradingDays().compute([], (), {}) == 0.0

    def test_single_snapshot(self):
        """单日：总收益/回撤有定义（0），年化类无定义 → NaN。"""
        snaps = _snapshots([10.0])
        assert TotalReturn().compute(snaps, (), {}) == pytest.approx(0.0)
        assert MaxDrawdown().compute(snaps, (), {}) == pytest.approx(0.0)
        assert math.isnan(AnnualizedReturn().compute(snaps, (), _cfg()))
        assert math.isnan(AnnualizedVolatility().compute(snaps, (), _cfg()))
        assert math.isnan(SharpeRatio().compute(snaps, (), _cfg()))
        assert math.isnan(WinRate().compute(snaps, (), _cfg()))
        assert math.isnan(TurnoverAnnualized().compute(snaps, _trades(), _cfg()))

    def test_flat_nav_zero_vol(self):
        """全平 NAV：波动为 0 → 夏普 NaN（除零降级）。"""
        snaps = _snapshots([10.0, 10.0, 10.0])
        assert AnnualizedVolatility().compute(snaps, (), _cfg()) == pytest.approx(0.0)
        assert math.isnan(SharpeRatio().compute(snaps, (), _cfg()))

    def test_monotonic_up_no_loss_no_drawdown(self):
        """单调上涨：无亏损日 → 盈亏比 NaN；无回撤 → 卡玛 NaN。"""
        snaps = _snapshots([10.0, 11.0, 12.0, 13.0])
        assert MaxDrawdown().compute(snaps, (), {}) == pytest.approx(0.0)
        assert math.isnan(ProfitLossRatio().compute(snaps, (), {}))
        assert math.isnan(CalmarRatio().compute(snaps, (), _cfg()))
        assert WinRate().compute(snaps, (), {}) == pytest.approx(1.0)

    def test_bad_config_factor(self):
        """annualization_factor ≤ 0 → NaN（不崩溃）。"""
        assert math.isnan(
            AnnualizedVolatility().compute(_snapshots(), (),
                                           {"annualization_factor": 0}))

    def test_compute_all_isolates_failures(self):
        """compute_all：单指标失败只该项 NaN，其余照算（不中断）。"""
        out = compute_all(_snapshots([10.0]), _trades(), _cfg())
        assert len(out) == 15
        assert out["n_trading_days"] == 1.0
        assert out["total_fees"] == pytest.approx(11.0)
        assert math.isnan(out["annualized_return"])
        assert math.isnan(out["sharpe_ratio"])

    def test_compute_all_full_run(self):
        out = compute_all(_snapshots(), _trades(), _cfg())
        assert set(out) == set(METRIC_NAMES)
        assert out["final_nav"] == pytest.approx(10_000.0)
        assert out["max_drawdown"] == pytest.approx(-0.25)


# ═════════════════════════════════════════════════════════════
# 加法禁区（N5）
# ═════════════════════════════════════════════════════════════
class TestAdditionForbidden:
    """回归（04 §8.2.6 N5-1 收益归集警示）：禁止 realized_pnl + 分红现金。

    数值例（文档）：成本 10、派息 1、除息后平价卖出@9 → realized_pnl=0、
    分红现金=200（200 股）、NAV 变化=0——三者不可相加；总收益以 NAV 口径
    为准（=0），若相加得 200，虚增收益（H4 同族双重计算）。
    """

    def _dividend_case(self) -> tuple[list[PortfolioSnapshot], float, float]:
        """买入 200@10 → 派息 1/股 → 除息后卖 100@9（平价）。

        返回（快照序列, realized_pnl, 分红现金）。
        """
        pf = Portfolio(cash=2_000.0)
        pf.apply_fill(mk_fill(OrderSide.BUY, 200, 10.0, 0.0))
        pf.mark_all({SYM: 10.0})
        snaps = [pf.snapshot(D("20150105"))]                    # tv=2000
        # 派息：ex 摊薄成本 10→9（不入现金）；pay 按登记 200 股到账 200 元
        action = CorporateAction(symbol=SYM, ex_date=D("20150106"),
                                 pay_date=D("20150106"), record_date=None,
                                 cash_div_per_share=1.0, stk_div_per_share=0.0,
                                 ann_date=None)
        qty_at_ex = pf.apply_ex_date(action)
        dividend_cash = pf.apply_pay_date(SYM, qty_at_ex, action.cash_div_per_share)
        pf.mark_all({SYM: 9.0})                                 # 除息后平价
        snaps.append(pf.snapshot(D("20150106")))                # tv=2000（未变）
        pf.advance_day()                                        # T+1 解冻可卖
        pf.apply_fill(mk_fill(OrderSide.SELL, 100, 9.0, 0.0))   # 平价卖出
        snaps.append(pf.snapshot(D("20150107")))                # tv=2000（未变）
        pos = pf.position(SYM)
        return snaps, pos.realized_pnl, dividend_cash

    def test_nav_unchanged_but_pnl_plus_dividend_inflated(self):
        snaps, realized, dividend = self._dividend_case()
        # ① NAV 全程 2000 → 总收益 0
        assert [s.total_value for s in snaps] == pytest.approx([2000.0] * 3)
        assert TotalReturn().compute(snaps, (), {}) == pytest.approx(0.0)
        # ② realized_pnl=0（成本已被派息摊薄至 9，卖 9 → 0）+ 分红现金 200
        assert realized == pytest.approx(0.0)
        assert dividend == pytest.approx(200.0)
        # ③ 禁区实证：相加 = 200 ≠ 总收益 0（双重计算）
        assert realized + dividend == pytest.approx(200.0)
        assert realized + dividend != pytest.approx(0.0)

    def test_metric_uses_nav_not_pnl(self):
        """指标只消费 NAV：快照里塞入 realized_pnl/分红干扰值不影响结果。"""

        @dataclass
        class TaintedSnap:
            date: TradingDate
            cash: float
            market_value: float
            total_value: float
            daily_return: float
            cumulative_return: float
            drawdown: float
            realized_pnl: float = 200.0        # 干扰：诱使相加
            dividend_cash: float = 200.0       # 干扰

        tainted = [TaintedSnap(D("20150105"), 0.0, 2000.0, 2000.0, 0.0, 0.0, 0.0),
                   TaintedSnap(D("20150106"), 200.0, 1800.0, 2000.0, 0.0, 0.0, 0.0),
                   TaintedSnap(D("20150107"), 1100.0, 900.0, 2000.0, 0.0, 0.0, 0.0)]
        assert TotalReturn().compute(tainted, (), {}) == pytest.approx(0.0)
        assert FinalNav().compute(tainted, (), {}) == pytest.approx(2000.0)

    def test_audit_covers_dividend_case(self):
        """分红场景快照仍与四式一致（NAV 四式不受派息两时点影响）。"""
        snaps, _, _ = self._dividend_case()
        assert audit_nav_four(snaps) == []


# ═════════════════════════════════════════════════════════════
# registry 扩展点 + 协议
# ═════════════════════════════════════════════════════════════
class TestRegistry:
    """ANALYZER 扩展点：15 名配置声明制 + 版本协商。"""

    def test_fifteen_metrics_registered(self):
        assert len(METRIC_NAMES) == 15
        # 内置 15 项必须全在注册表；**允许扩展注册**（19 号 EX-1：
        # 自定义 Analyzer 注册后生效——名字表被扩展是预期行为）
        assert set(METRIC_NAMES) <= set(available(ANALYZER))

    def test_create_all_names(self):
        for name in METRIC_NAMES:
            metric = create(ANALYZER, name)
            assert metric.name == name
            assert metric.contract_version == CONTRACT_VERSION
            assert isinstance(metric, MetricBase)
            # 全名均可计算（空输入也不抛——降级为 NaN）
            assert isinstance(metric.compute([], (), {}), float)

    def test_resolve_unknown_name(self):
        with pytest.raises(RegistryError, match="未知插件名"):
            create(ANALYZER, "not_a_metric")

    def test_metric_bases_are_subclasses(self):
        assert issubclass(SharpeRatio, MetricBase)
        assert all(issubclass(c, MetricBase) and c.contract_version == CONTRACT_VERSION
                   for c in (FinalNav, TotalReturn, AnnualizedReturn,
                             AnnualizedVolatility, SharpeRatio, MaxDrawdown,
                             CalmarRatio, WinRate, ProfitLossRatio, NFills,
                             TotalFees, TotalTurnover, TurnoverAnnualized,
                             FeeRatio, NTradingDays))

    def test_nav_series_is_frozen(self):
        """基座不可变（纯函数产物，可安全共享/缓存）。"""
        s: NavSeries = build_nav_series(_snapshots())
        with pytest.raises(FrozenInstanceError):
            s.nav = ()  # type: ignore[misc]

    def test_nan_sentinel(self):
        assert math.isnan(NAN)
