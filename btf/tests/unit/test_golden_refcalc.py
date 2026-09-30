# -*- coding: utf-8 -*-
"""独立参考计算器单测（M2 任务 5.6；09 §14.2 G1 第一断言=名义入账四式）。

覆盖：
    1. **独立性纪律**：参考计算器不 import btf（回归有效性前提）；
    2. 入账四式（买/卖/现金/重估）数值=手算；
    3. 事件调整两时点：派息（pay≡ex 单日 Δ=0；ex≠pay 窗口合并 Δ=0）、
       送转+派息混合合成式；
    4. NAV 四式（04 §8.2.6 N5-1）；
    5. 分段费率（印花税 2023-08-28 两段；过户费沪深分段）；
    6. 加法禁区（N5）：realized_pnl + 分红现金 ≠ 总收益。
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

import golden_refcalc as ref

pytestmark = [pytest.mark.l1]

SYM = "000001.SZ"


def _bars(dates: list[str], closes: list[float], opens: list[float] | None = None
          ) -> list[dict]:
    opens = opens or closes
    return [{"symbol": SYM, "date": d, "open": o, "high": max(o, c),
             "low": min(o, c), "close": c, "pre_close": 0.0, "vol": 1e6,
             "amount": 1e7}
            for d, o, c in zip(dates, opens, closes, strict=True)]


def _data(dates, closes, actions=None, opens=None) -> dict:
    return {
        "dates": dates,
        "bars": _bars(dates, closes, opens),
        "actions": actions or [],
        "instruments": [{"symbol": SYM, "asset_class": "stock",
                         "board": "main", "lot_size": 100}],
    }


def _plan(dates: list[str], weight: float = 0.9, cash: float = 100_000.0) -> dict:
    return {"initial_cash": cash, "lot_size": 100,
            "targets": {dates[0]: {SYM: weight},
                        **({dates[-2]: {}} if len(dates) > 2 else {})}}


# ═════════════════════════════════════════════════════════════
class TestIndependence:
    """09 §14.3：参考计算器必须独立于引擎实现。"""

    def test_no_btf_import(self):
        """源码无 btf 导入语句（docstring 中提及"不 import btf"不算）。"""
        lines = Path(ref.__file__).read_text(encoding="utf-8").splitlines()
        assert not [ln for ln in lines
                    if ln.strip().startswith(("import btf", "from btf"))]

    def test_pure_stdlib_only(self):
        """仅标准库（math/typing）——不引入第三方计算栈。"""
        src = Path(ref.__file__).read_text(encoding="utf-8")
        for banned in ("import numpy", "import pandas", "import pyarrow"):
            assert banned not in src


class TestFeeSegments:
    """分段费率（与引擎 A_SHARE_SEGMENTS 同规则，独立实现）。"""

    def test_sell_after_stamp_halved(self):
        """2023-08-28 起印花税 0.05%：1 万×0.0005=5.0"""
        fee = ref.fees_of(SYM, "sell", 1_000, 10.0, "20230828")
        assert fee["stamp_duty"] == pytest.approx(5.0)
        assert fee["commission"] == pytest.approx(5.0)      # 最低佣金 5 元
        assert fee["transfer_fee"] == pytest.approx(0.1)    # 成交额 0.001%
        assert fee["total"] == pytest.approx(10.1)

    def test_sell_before_stamp_full(self):
        fee = ref.fees_of(SYM, "sell", 1_000, 10.0, "20230827")
        assert fee["stamp_duty"] == pytest.approx(10.0)
        assert fee["total"] == pytest.approx(15.1)

    def test_buy_no_stamp(self):
        assert ref.fees_of(SYM, "buy", 1_000, 10.0, "20230828")["stamp_duty"] == 0.0

    def test_transfer_sh_only_before_2015(self):
        """2015-08 前：仅沪市按面额 0.6‰；深市不收。"""
        assert ref.fees_of(SYM, "buy", 1_000, 10.0, "20140701")["transfer_fee"] == 0.0
        assert ref.fees_of("600000.SH", "buy", 1_000, 10.0,
                           "20140701")["transfer_fee"] == pytest.approx(0.6)

    def test_commission_min_five_yuan(self):
        """小额成交触发最低佣金 5 元（成交额 1000 → 费率仅 0.25 元）。"""
        assert ref.fees_of(SYM, "buy", 100, 10.0, "20230828")["commission"] == 5.0

    def test_rounding_to_cent(self):
        fee = ref.fees_of(SYM, "sell", 700, 13.37, "20230828")
        assert all(round(v, 2) == v for v in fee.values())


class TestAccountingFourForms:
    """入账四式（04 §8.2.6）数值=手算。"""

    def test_buy_form_and_cash(self):
        """买 9000@10（2020 段：佣金 22.5 + 过户 0.002%×9 万=1.8）：fee=24.3"""
        dates = ["20200526", "20200527", "20200528", "20200529"]
        closes = [10.0, 10.0, 10.0, 10.0]
        out = ref.compute_case(_data(dates, closes), _plan(dates))
        buy = out["fills"][0]
        assert buy["qty"] == 9_000 and buy["price"] == pytest.approx(10.0)
        # commission=max(90000×2.5e-4=22.5,5)=22.5；transfer=90000×2e-5=1.8
        assert buy["fee"]["commission"] == pytest.approx(22.5)
        assert buy["fee"]["transfer_fee"] == pytest.approx(1.8)
        assert buy["fee"]["total"] == pytest.approx(24.3)
        # avg_cost = (0 + 10×9000 + 24.3)/9000
        assert out["snapshots"][1]["avg_cost"][SYM] == pytest.approx(
            (90_000 + 24.3) / 9_000)
        assert out["snapshots"][1]["cash"] == pytest.approx(100_000 - 90_000 - 24.3)

    def test_sell_form_realized_pnl(self):
        """末日开盘卖出：realized = (price − avg_cost)×qty − fee（含买费摊入）。"""
        dates = ["20200526", "20200527", "20200528", "20200529"]
        out = ref.compute_case(_data(dates, [10.0] * 4), _plan(dates))
        sell = out["fills"][-1]
        avg_cost = (90_000 + 24.3) / 9_000
        expect = (10.0 - avg_cost) * 9_000 - sell["fee"]["total"]
        assert sell["side"] == "sell" and sell["qty"] == 9_000
        assert sell["fee"]["stamp_duty"] == pytest.approx(90.0)  # 2020 段 0.1%
        assert out["invariants"]["realized_pnl_total"] == pytest.approx(expect)

    def test_mark_to_market_unrealized(self):
        """重估：持仓按 close 估值——价格 10→11 时 NAV 增 9000。"""
        dates = ["20200526", "20200527", "20200528"]
        out = ref.compute_case(_data(dates, [10.0, 10.0, 11.0]),
                               {"initial_cash": 100_000.0, "lot_size": 100,
                                "targets": {dates[0]: {SYM: 0.9}}})
        assert out["snapshots"][2]["market_value"] == pytest.approx(99_000.0)


class TestNavFourForms:
    """NAV 四式（04 §8.2.6 N5-1）。"""

    def test_first_day_return_zero_and_cumulative(self):
        dates = ["20200526", "20200527", "20200528"]
        out = ref.compute_case(_data(dates, [10.0, 11.0, 12.0]),
                               {"initial_cash": 100_000.0, "lot_size": 100,
                                "targets": {dates[0]: {SYM: 0.9}}})
        snaps = out["snapshots"]
        assert snaps[0]["daily_return"] == 0.0
        assert snaps[0]["cumulative_return"] == 0.0
        tv0, tv1 = snaps[0]["total_value"], snaps[1]["total_value"]
        assert snaps[1]["daily_return"] == pytest.approx(tv1 / tv0 - 1.0)
        assert snaps[2]["cumulative_return"] == pytest.approx(
            snaps[2]["total_value"] / tv0 - 1.0)

    def test_total_value_is_cash_plus_market_value(self):
        dates = ["20200526", "20200527"]
        out = ref.compute_case(_data(dates, [10.0, 10.0]),
                               {"initial_cash": 100_000.0, "lot_size": 100,
                                "targets": {dates[0]: {SYM: 0.9}}})
        for snap in out["snapshots"]:
            assert snap["total_value"] == pytest.approx(
                snap["cash"] + snap["market_value"])

    def test_drawdown_from_running_peak(self):
        """第四式：dd_t = tv_t / max(tv_0..t) − 1（峰值逐日滚动）。

        价格路径取 [10, 10, 12, 11]：决策日收盘 10 → 次日开盘 10 成交（不透支；
        原夹具用 [10, 12, …] 会因「决策价 10 / 成交价 12」触发**资金不足拒单**
        ——参考计算器已补齐拒单链，与引擎同口径）。
        """
        dates = ["20200526", "20200527", "20200528", "20200529"]
        out = ref.compute_case(_data(dates, [10.0, 10.0, 12.0, 11.0]),
                               {"initial_cash": 100_000.0, "lot_size": 100,
                                "targets": {dates[0]: {SYM: 0.9}}})
        snaps = out["snapshots"]
        peak = 0.0
        for snap in snaps:
            peak = max(peak, snap["total_value"])
            assert snap["drawdown"] == pytest.approx(snap["total_value"] / peak - 1.0)
        assert snaps[-1]["drawdown"] < 0.0          # 未回到峰值


class TestEventAdjustments:
    """G1 核心：事件调整两时点（v0.3 N7-A）。"""

    def test_cash_div_pay_eq_ex_single_day_delta_zero(self):
        """派息 pay≡ex 且价格同额下调 → 单日 NAV Δ=0（黄金断言）。"""
        dates = ["20200526", "20200527", "20200528", "20200529"]
        closes = [10.0, 10.0, 9.5, 9.5]        # 除息日价格下调 0.5
        actions = [{"symbol": SYM, "ex_date": "20200528", "pay_date": "20200528",
                    "record_date": None, "cash_div_per_share": 0.5,
                    "stk_div_per_share": 0.0}]
        out = ref.compute_case(_data(dates, closes, actions), _plan(dates))
        snaps = {s["date"]: s for s in out["snapshots"]}
        # ex 日：avg_cost 摊薄 0.5；pay 日同日到账 9000×0.5=4500
        assert out["events"][0]["avg_cost_before"] - \
            out["events"][0]["avg_cost_after"] == pytest.approx(0.5)
        assert out["events"][1]["cash_delta"] == pytest.approx(4_500.0)
        # 价格跌 0.5×9000=4500 与现金到账 4500 相抵 → NAV 不变
        assert snaps["20200528"]["total_value"] == pytest.approx(
            snaps["20200527"]["total_value"])

    def test_cash_div_ex_ne_pay_window_delta_zero(self):
        """派息 ex≠pay：ex 日 −qty×div，pay 日 +qty×div，窗口合并 Δ=0。"""
        # 建仓成交在 d1（T+1 开盘）→ ex 须在其后（d2），窗口 d2(ex)→d3(pay)
        dates = ["19991015", "19991018", "19991019", "19991022", "19991025"]
        closes = [10.0, 10.0, 9.4, 9.4, 9.4]     # ex 日价格下调 0.6
        actions = [{"symbol": SYM, "ex_date": dates[2], "pay_date": dates[3],
                    "record_date": None, "cash_div_per_share": 0.6,
                    "stk_div_per_share": 0.0}]
        out = ref.compute_case(_data(dates, closes, actions), _plan(dates))
        snaps = {s["date"]: s for s in out["snapshots"]}
        ex = next(e for e in out["events"] if e["type"] == "ex")
        pay = next(e for e in out["events"] if e["type"] == "pay")
        assert ex["nav_delta"] == pytest.approx(-9000 * 0.6)
        assert pay["cash_delta"] == pytest.approx(9000 * 0.6)
        # ex 日 NAV 缺口 −5400；pay 日补回 +5400 → 窗口合并 Δ=0
        assert snaps[dates[2]]["total_value"] == pytest.approx(
            snaps[dates[1]]["total_value"] - 5_400.0)
        assert snaps[dates[3]]["total_value"] == pytest.approx(
            snaps[dates[1]]["total_value"])
        window = out["invariants"]["windows"][0]
        assert (window["ex_date"], window["pay_date"]) == (dates[2], dates[3])
        assert window["window_event_delta"] == pytest.approx(0.0)

    def test_stk_div_mixed_formula(self):
        """混合行动合成式：qty×1.5、avg_cost'=(avg−cash)/(1+stk)。"""
        dates = ["19940708", "19940711", "19940712", "19940714", "19940715"]
        actions = [{"symbol": SYM, "ex_date": dates[2], "pay_date": dates[3],
                    "record_date": None, "cash_div_per_share": 0.5,
                    "stk_div_per_share": 0.5}]
        # 除权日价格按 (pre−0.5)/1.5 = 6.333…（10 元基价）
        closes = [10.0, 10.0, 6.333333333333333, 6.333333333333333,
                  6.333333333333333]
        out = ref.compute_case(_data(dates, closes, actions), _plan(dates))
        ev = next(e for e in out["events"] if e["type"] == "ex")
        avg_before = ev["avg_cost_before"]
        assert ev["qty_after"] == round(9_000 * 1.5)
        assert ev["avg_cost_after"] == pytest.approx((avg_before - 0.5) / 1.5)

    def test_no_position_action_is_noop(self):
        """无持仓 → 事件 no-op（引擎同语义：不产生 ex/pay 记录）。"""
        dates = ["20200526", "20200527"]
        actions = [{"symbol": SYM, "ex_date": "20200526", "pay_date": "20200526",
                    "record_date": None, "cash_div_per_share": 0.5,
                    "stk_div_per_share": 0.0}]
        out = ref.compute_case(_data(dates, [10.0, 10.0], actions),
                               {"initial_cash": 100_000.0, "lot_size": 100,
                                "targets": {}})
        assert out["events"] == []

    def test_dividend_qty_is_registered_not_sold_down(self):
        """派息按**登记数量**（qty_at_ex）——窗口内卖出不剥夺分红权（C1）。"""
        dates = ["19991015", "19991018", "19991019", "19991022", "19991025"]
        closes = [10.0, 10.0, 9.4, 9.4, 9.4]
        actions = [{"symbol": SYM, "ex_date": dates[2], "pay_date": dates[3],
                    "record_date": None, "cash_div_per_share": 0.6,
                    "stk_div_per_share": 0.0}]
        # ex 日（d2）收盘决策清仓 → pay 日（d3）卖出成交：分红权不因卖出剥夺
        plan = {"initial_cash": 100_000.0, "lot_size": 100,
                "targets": {dates[0]: {SYM: 0.9}, dates[2]: {}}}
        out = ref.compute_case(_data(dates, closes, actions), plan)
        pay = next(e for e in out["events"] if e["type"] == "pay")
        # 卖出成交在 ex 日（19 日卖单→18 日？）：持仓已清仍按登记数量发放
        assert pay["qty_at_ex"] == 9_000
        assert pay["cash_delta"] == pytest.approx(9_000 * 0.6)


class TestAdditionForbidden:
    """N5 加法禁区：realized_pnl + 分红现金 ≠ 总收益。"""

    def test_pnl_plus_dividend_not_equal_total_return(self):
        dates = ["20200526", "20200527", "20200528", "20200529"]
        closes = [10.0, 10.0, 9.5, 9.5]
        actions = [{"symbol": SYM, "ex_date": "20200528", "pay_date": "20200528",
                    "record_date": None, "cash_div_per_share": 0.5,
                    "stk_div_per_share": 0.0}]
        out = ref.compute_case(_data(dates, closes, actions), _plan(dates))
        realized = out["invariants"]["realized_pnl_total"]
        dividend = sum(e.get("cash_delta", 0.0) for e in out["events"]
                       if e["type"] == "pay")
        nav_delta = (out["snapshots"][-1]["total_value"]
                     - out["snapshots"][0]["total_value"])
        # 相加 ≠ NAV 口径总变化（双重计算）——禁区实证
        assert realized + dividend != pytest.approx(nav_delta)
        assert dividend == pytest.approx(4_500.0)


class TestMetrics:
    """指标（独立实现，口径同 04 §8.2.6）。"""

    def test_core_metrics_present(self):
        dates = ["20200526", "20200527", "20200528", "20200529"]
        out = ref.compute_case(_data(dates, [10.0, 11.0, 9.0, 10.0]),
                               _plan(dates))
        m = out["metrics"]
        for key in ("final_nav", "total_return", "annualized_return",
                    "annualized_volatility", "sharpe_ratio", "max_drawdown",
                    "calmar_ratio", "win_rate", "profit_loss_ratio", "n_fills",
                    "total_fees", "total_turnover", "n_trading_days"):
            assert key in m, key
        assert m["n_fills"] == 2.0
        assert m["n_trading_days"] == 4.0

    def test_flat_series_degrades_to_nan(self):
        dates = ["20200526", "20200527"]
        out = ref.compute_case(_data(dates, [10.0, 10.0]),
                               {"initial_cash": 100_000.0, "lot_size": 100,
                                "targets": {}})
        assert math.isnan(out["metrics"]["sharpe_ratio"])
        assert out["metrics"]["total_return"] == pytest.approx(0.0)
