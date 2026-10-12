# -*- coding: utf-8 -*-
"""L1 单测：Position/Portfolio 名义记账（PoC-2 任务 2.3 验收）。

验收锚点：
    - 守恒式：每 Fill 后 Δcash + ΔMV(名义) + fee = 0（与 adj_factor 无关）；
    - 四式数值=手算基准（评审实证脚本输出的 1000≠0 不复现案例）；
    - T+1 冻结/解冻与 T+0 ETF 立即可用。
"""
from __future__ import annotations

import pytest
from btf.domain.orders import Fee, Fill, OrderSide
from btf.domain.types import TradingDate
from btf.portfolio.portfolio import CASH_KEY, Portfolio

pytestmark = [pytest.mark.l1]

D = TradingDate.from_ymd("20150105")


def mk_fill(symbol: str, side: OrderSide, qty: int, price: float,
            fee_total: float = 0.0, fid: str = "F00000001") -> Fill:
    return Fill(fill_id=fid, order_id="O00000001", symbol=symbol, side=side,
                qty=qty, price=price,
                fee=Fee(commission=fee_total, stamp_duty=0.0,
                        transfer_fee=0.0, total=fee_total),
                fill_date=D, fill_timing="open")


class TestAccountingFourForms:
    """四式数值：手算基准（04 §8.2.6 公式块逐行对照）。"""

    def setup_method(self):
        self.pf = Portfolio(cash=100_000.0)

    def test_buy_form(self):
        """买 100@10.0 fee 5：avg_cost=(0+10×100+5)/100=10.05；cash=98995。"""
        self.pf.apply_fill(mk_fill("000001.SZ", OrderSide.BUY, 100, 10.0, 5.0))
        pos = self.pf.position("000001.SZ")
        assert pos.qty == 100
        assert pos.avg_cost == pytest.approx(10.05)
        assert self.pf.cash == pytest.approx(98_995.0)

    def test_sell_form(self):
        """买 100@10 fee5 → 卖 60@11 fee5：realized=(11−10.05)×60−5=52.0。"""
        self.pf.apply_fill(mk_fill("000001.SZ", OrderSide.BUY, 100, 10.0, 5.0))
        self.pf.advance_day()                                # 解冻（四式测试无关 T+1）
        self.pf.apply_fill(mk_fill("000001.SZ", OrderSide.SELL, 60, 11.0, 5.0, "F2"))
        pos = self.pf.position("000001.SZ")
        assert pos.qty == 40
        assert pos.realized_pnl == pytest.approx((11.0 - 10.05) * 60 - 5.0)  # 52.0
        assert self.pf.cash == pytest.approx(98_995.0 + 660.0 - 5.0)         # 99_650

    def test_mark_form(self):
        """重估：unrealized=(close−avg_cost)×qty；买后 mark@10 → −5（费用吸收）。"""
        self.pf.apply_fill(mk_fill("000001.SZ", OrderSide.BUY, 100, 10.0, 5.0))
        self.pf.mark_all({"000001.SZ": 10.0})
        pos = self.pf.position("000001.SZ")
        assert pos.unrealized_pnl == pytest.approx((10.0 - 10.05) * 100)     # −5.0
        assert self.pf.market_value() == pytest.approx(1_000.0)
        assert self.pf.total_value() == pytest.approx(98_995.0 + 1_000.0)

    def test_cash_form_buy_and_sell(self):
        self.pf.apply_fill(mk_fill("000001.SZ", OrderSide.BUY, 100, 10.0, 5.0))
        assert self.pf.cash == pytest.approx(100_000.0 - (1_000.0 + 5.0))
        self.pf.advance_day()
        self.pf.apply_fill(mk_fill("000001.SZ", OrderSide.SELL, 100, 10.5, 3.0, "F2"))
        assert self.pf.cash == pytest.approx(98_995.0 + (1_050.0 - 3.0))
        assert "000001.SZ" not in self.pf.positions           # 空仓即移除


class TestConservationInvariant:
    """守恒式：Δcash + ΔMV(名义) + fee = 0，每笔 Fill 后恒成立（af 无关）。"""

    def test_conservation_per_fill(self):
        """守恒式（成交时点口径）：Δcash + Δqty×fill_price + fee = 0，
        残余持仓按原 mark 价不动（mark 重估属步骤⑤，不进守恒式）。"""
        pf = Portfolio(cash=100_000.0)
        fills = [
            mk_fill("000001.SZ", OrderSide.BUY, 100, 10.0, 5.0),
            mk_fill("600519.SH", OrderSide.BUY, 100, 200.0, 40.0, "F2"),
            mk_fill("000001.SZ", OrderSide.SELL, 60, 10.8, 2.0, "F3"),
            mk_fill("600519.SH", OrderSide.SELL, 50, 205.0, 10.0, "F4"),
        ]
        for f in fills:
            cash_before = pf.cash
            pos_before = pf.position(f.symbol)
            qty_before = 0 if pos_before is None else pos_before.qty
            if f.side is OrderSide.SELL:
                pf.advance_day()                      # 解冻：守恒断言与 T+1 正交
            pf.apply_fill(f)
            pos_after = pf.position(f.symbol)
            qty_after = 0 if pos_after is None else pos_after.qty
            d_cash = pf.cash - cash_before
            d_mv = (qty_after - qty_before) * f.price  # 成交部分按成交价
            assert d_cash + d_mv + f.fee.total == pytest.approx(0.0, abs=1e-9)
        # 终值手算基准：现金流水逐笔精确（无泄漏）
        cash_expected = (100_000.0 - 1_005.0 - 20_040.0
                         + 648.0 - 2.0 + 10_250.0 - 10.0)
        assert pf.cash == pytest.approx(cash_expected, abs=1e-9)   # 89_841
        pf.mark_all({"000001.SZ": 10.8, "600519.SH": 205.0})
        assert pf.market_value() == pytest.approx(40 * 10.8 + 50 * 205.0)
        assert pf.total_value() == pytest.approx(cash_expected + 40 * 10.8 + 50 * 205.0)

    def test_adj_factor_irrelevance(self):
        """名义记账下复权因子不放大误差（评审实证：1000≠0 不复现）。

        同一组成交在任意价格水平（模拟不同复权基准）：终值与手算现金
        逐分钱一致（浮点零累积），af 缩放不产生额外泄漏。
        """
        for scale in (1.0, 2.0, 33.7):        # af=1/2/33.7 的价格缩放代理
            pf = Portfolio(cash=1_000_000.0)
            pf.apply_fill(mk_fill("600519.SH", OrderSide.BUY, 100, 10.0 * scale, 5.0))
            pf.advance_day()
            pf.apply_fill(mk_fill("600519.SH", OrderSide.SELL, 100, 10.5 * scale, 5.0, "F2"))
            # 空仓 → tv=cash；价差盈利 50×scale 恰入账，仅亏两笔费用
            assert pf.total_value() == pytest.approx(1_000_000.0 + 50.0 * scale - 10.0,
                                                     abs=1e-9)


class TestTPlusOne:
    def test_buy_frozen_same_day(self):
        pf = Portfolio(cash=100_000.0)
        pf.apply_fill(mk_fill("000001.SZ", OrderSide.BUY, 100, 10.0))
        assert pf.available_qty("000001.SZ") == 0            # T+1 冻结
        with pytest.raises(RuntimeError, match="卖出违例"):
            pf.apply_fill(mk_fill("000001.SZ", OrderSide.SELL, 100, 10.0, fid="F2"))

    def test_advance_day_unfreezes(self):
        pf = Portfolio(cash=100_000.0)
        pf.apply_fill(mk_fill("000001.SZ", OrderSide.BUY, 100, 10.0))
        pf.advance_day()
        assert pf.available_qty("000001.SZ") == 100
        pf.apply_fill(mk_fill("000001.SZ", OrderSide.SELL, 100, 10.0, fid="F2"))
        assert "000001.SZ" not in pf.positions

    def test_t0_etf_immediately_available(self):
        pf = Portfolio(cash=100_000.0)
        pf.apply_fill(mk_fill("511380.SH", OrderSide.BUY, 100, 100.0), t_plus=0)
        assert pf.available_qty("511380.SH") == 100
        pf.apply_fill(mk_fill("511380.SH", OrderSide.SELL, 100, 100.1, fid="F2"), t_plus=0)

    def test_partial_sell_updates_available(self):
        pf = Portfolio(cash=100_000.0)
        pf.apply_fill(mk_fill("000001.SZ", OrderSide.BUY, 200, 10.0))
        pf.advance_day()
        pf.apply_fill(mk_fill("000001.SZ", OrderSide.SELL, 60, 10.0, fid="F2"))
        assert pf.available_qty("000001.SZ") == 140
        assert pf.position("000001.SZ").qty == 140


class TestSnapshotNavForms:
    """NAV 四式（N5-1）：daily/cumulative/drawdown 手算基准。"""

    def test_nav_series(self):
        pf = Portfolio(cash=100_000.0)
        pf.apply_fill(mk_fill("000001.SZ", OrderSide.BUY, 1_000, 10.0))
        pf.mark_all({"000001.SZ": 10.0})
        s1 = pf.snapshot(D)                                  # tv=100000
        assert s1.daily_return == 0.0 and s1.cumulative_return == 0.0
        assert s1.drawdown == 0.0
        assert s1.weights["000001.SZ"] == pytest.approx(0.1)
        assert s1.weights[CASH_KEY] == pytest.approx(0.9)

        pf.mark_all({"000001.SZ": 11.0})
        s2 = pf.snapshot(D)                                  # tv=101000
        assert s2.daily_return == pytest.approx(0.01)
        assert s2.cumulative_return == pytest.approx(0.01)
        assert s2.drawdown == 0.0

        pf.mark_all({"000001.SZ": 9.0})
        s3 = pf.snapshot(D)                                  # tv=99000
        assert s3.daily_return == pytest.approx(99_000 / 101_000 - 1)
        assert s3.cumulative_return == pytest.approx(-0.01)
        assert s3.drawdown == pytest.approx(99_000 / 101_000 - 1)

        pf.mark_all({"000001.SZ": 10.0})
        s4 = pf.snapshot(D)                                  # tv=100000 < peak 101000
        assert s4.drawdown == pytest.approx(100_000 / 101_000 - 1)
        assert s4.cumulative_return == pytest.approx(0.0)


class TestCorporateActionForms:
    """公司行动两时点公式（PoC-3 任务 3.2；04 §8.2.6 S1 逐行对照）。

    avg_cost 精确手算锚点（G1 黄金断言的单元层补集——集成层守恒式
    验证 qty/cash/TV，本类直接断言 avg_cost 数值）。
    """

    def setup_method(self):
        self.pf = Portfolio(cash=100_000.0)
        self.pf.apply_fill(mk_fill("600900.SH", OrderSide.BUY, 100, 10.0))

    @staticmethod
    def _action(cash_div=0.0, stk_div=0.0, symbol="600900.SH"):
        from btf.domain.action import CorporateAction
        return CorporateAction(
            symbol=symbol, ex_date=D, pay_date=None, record_date=None,
            cash_div_per_share=cash_div, stk_div_per_share=stk_div,
            ann_date=None)

    def test_pure_stk_div(self):
        """10送10：qty 100→200、avg_cost 10→5、available_qty 200；
        返回 qty_at_ex=100（登记股数=送转前）。

        引擎真实顺序：advance_day 解冻（100）→ 送转扩张（200）——
        ex 在新交易日开盘前，available_qty=qty 基础上同步 ×2。
        """
        self.pf.advance_day()
        qty_at_ex = self.pf.apply_ex_date(self._action(stk_div=1.0))
        pos = self.pf.position("600900.SH")
        assert qty_at_ex == 100
        assert pos.qty == 200
        assert pos.available_qty == 200          # 实现注意①（解冻后扩张）
        assert pos.avg_cost == pytest.approx(5.0)

    def test_pure_cash_div_ex(self):
        """派 1 元：avg_cost 10→9（ex 只摊成本，现金不动）；
        qty_at_ex=100。（setup 后 cash=99_000）"""
        qty_at_ex = self.pf.apply_ex_date(self._action(cash_div=1.0))
        pos = self.pf.position("600900.SH")
        assert qty_at_ex == 100
        assert pos.qty == 100
        assert pos.avg_cost == pytest.approx(9.0)
        assert self.pf.cash == pytest.approx(99_000.0)   # 未到账

    def test_mixed_ex_date(self):
        """混合（10送10+派1）：avg_cost=(10−1)/2=4.5（A 股除权价公式
        同构——先派息后送转，总成本账平 200×4.5=900=1000−100）。"""
        qty_at_ex = self.pf.apply_ex_date(
            self._action(cash_div=1.0, stk_div=1.0))
        pos = self.pf.position("600900.SH")
        assert qty_at_ex == 100                  # 分红按送转前股数登记
        assert pos.qty == 200
        assert pos.avg_cost == pytest.approx(4.5)

    def test_pay_date_cash(self):
        """pay 到账：cash += qty_at_ex×cash_div（登记数，与当前持仓无关）。"""
        self.pf.apply_ex_date(self._action(cash_div=1.0))
        got = self.pf.apply_pay_date("600900.SH", 100, 1.0)
        assert got == pytest.approx(100.0)
        assert self.pf.cash == pytest.approx(99_100.0)

    def test_no_position_noop(self):
        """无持仓标的：ex no-op 返回 0。"""
        assert self.pf.apply_ex_date(self._action(cash_div=1.0,
                                                  symbol="000001.SZ")) == 0

    def test_fractional_stk_div_rounding(self):
        """千分位送转比例（如 10 送 1.235）：qty 取整（**银行家舍入**，B-01 订正）；
        avg_cost 按文档理论比例 /=（整比例场景两者精确一致）。"""
        qty_at_ex = self.pf.apply_ex_date(self._action(stk_div=0.1235))
        pos = self.pf.position("600900.SH")
        assert qty_at_ex == 100
        assert pos.qty == round(100 * 1.1235)    # 112
        assert pos.avg_cost == pytest.approx(10.0 / 1.1235)

    def test_stk_div_half_boundary_pins_rounding(self):
        """**B-01 黄金用例**：送转 `.5` 边界行为**钉死**（ADR-20 第 ⑪ 条补注）。

        口径（2026-10-11 三轮架构审查 B-01 订正注释）：`round()` ＝
        **银行家舍入**（.5 进偶数），**不是**「四舍五入」。
        本用例同时覆盖"进位"与"退位"两侧 —— 任何把口径改成 half-up（151.5→152
        两者同号看不出差别）或 floor 的改动都会在此失败；**改口径属口径变更**，
        须走 CHANGELOG 单列 ＋ 变化清单（照 19 号 §120.2 先例）。

        qty=101、10送5（stk_div=0.5）：101×1.5 = 151.5 → round → **152**（偶）
        qty=103、10送5：              103×1.5 = 154.5 → round → **154**（偶）
        """
        pf_a = Portfolio(cash=1_000_000.0)
        pf_a.apply_fill(mk_fill("600900.SH", OrderSide.BUY, 101, 10.0))
        pf_a.apply_ex_date(self._action(stk_div=0.5))
        assert pf_a.position("600900.SH").qty == 152

        pf_b = Portfolio(cash=1_000_000.0)
        pf_b.apply_fill(mk_fill("600900.SH", OrderSide.BUY, 103, 10.0))
        pf_b.apply_ex_date(self._action(stk_div=0.5))
        assert pf_b.position("600900.SH").qty == 154

    def test_rights_p0_fallback_source_is_disclosed(self):
        """**B-02 黄金用例**：配股 `p0` 兜底（成本冒充市价）**必须可披露**。

        当日无 bar（`_last_close` 无该标的）⇒ `p0` 取 `avg_cost`，事件须带
        `p0_source="avg_cost_fallback"`（报告层 `_corp_action_notes` 据此
        进「假设与披露」）；有 `last_close` ⇒ `p0_source="last_close"`。
        """
        from btf.domain.action import CorporateAction

        def _rights_action():
            return CorporateAction(
                symbol="600900.SH", ex_date=D, pay_date=None, record_date=None,
                cash_div_per_share=0.0, stk_div_per_share=0.0, ann_date=None,
                rights_ratio=0.5, rights_price=5.0)

        # ① 无 last_close（无 bar）⇒ 兜底取 avg_cost（=10.0）
        pf = Portfolio(cash=1_000_000.0)
        pf.apply_fill(mk_fill("600900.SH", OrderSide.BUY, 100, 10.0))
        pf.apply_ex_date(_rights_action())
        ev = pf.rights_events[-1]
        assert ev["p0_source"] == "avg_cost_fallback"
        assert ev["p0"] == pytest.approx(10.0)

        # ② 有 last_close ⇒ 正常路径（不得误标降级）
        pf2 = Portfolio(cash=1_000_000.0)
        pf2.apply_fill(mk_fill("600900.SH", OrderSide.BUY, 100, 10.0))
        pf2.mark_all({"600900.SH": 12.0})
        pf2.apply_ex_date(_rights_action())
        assert pf2.rights_events[-1]["p0_source"] == "last_close"
        assert pf2.rights_events[-1]["p0"] == pytest.approx(12.0)
