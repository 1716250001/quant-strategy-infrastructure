# -*- coding: utf-8 -*-
"""L1 单测：RC-12（2026-10-05）规模相关滑点 `sqrt_impact`。

公式：`impact = min(η·σ·√(Q/V), cap)`；买 `ref×(1+impact)` / 卖 `ref×(1−impact)`。
覆盖：公式数值 / √ 规模律 / 上限 / 边界 / 参数校验 / 注册与装配接线 /
渲染串 / **默认不启用**（缺省仍 `pct 0.001`——本模型是成本敏感性研究的显式选择）。
"""
from __future__ import annotations

import math

import pytest
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.market import Bar, TradingState
from btf.domain.orders import Order, OrderSide, OrderType
from btf.domain.types import TradingDate
from btf.execution.cost import SqrtImpactSlippage

pytestmark = [pytest.mark.l1]

D = TradingDate.from_ymd("20150106")
FEED_NAME = "rc12_sqrt_feed"


def _order(side: OrderSide = OrderSide.BUY, qty: int = 50_000) -> Order:
    return Order(order_id="O1", symbol="000001.SZ", side=side,
                 order_type=OrderType.MARKET, qty=qty, limit_price=None,
                 created_at=D)


def _bar(volume: float = 1_000_000.0) -> Bar:
    return Bar(symbol="000001.SZ", date=D, open=10.0, high=10.0, low=10.0,
               close=10.0, volume=volume, amount=10.0 * volume, pre_close=10.0)


def _state() -> TradingState:
    return TradingState(symbol="000001.SZ", date=D, limit_up_price=11.0,
                        limit_down_price=9.0, is_suspended=False, is_st=False,
                        is_delisted=False, is_limit_up=False, is_limit_down=False)


class TestFormula:
    def test_buy_price_formula(self):
        model = SqrtImpactSlippage(eta=0.1, sigma=0.02)
        impact = 0.1 * 0.02 * math.sqrt(50_000 / 1_000_000)
        price = model.apply(_order(qty=50_000), 10.0, _state(), _bar())
        assert price == pytest.approx(10.0 * (1 + impact), rel=1e-12)

    def test_sell_price_formula(self):
        model = SqrtImpactSlippage(eta=0.1, sigma=0.02)
        impact = model.impact_of(50_000, 1_000_000)
        price = model.apply(_order(OrderSide.SELL, 50_000), 10.0, _state(),
                            _bar())
        assert price == pytest.approx(10.0 * (1 - impact), rel=1e-12)

    def test_cap_applied(self):
        """上限硬截断（防小票极端值）：impact_cap=1% ⇒ 冲击恒 ≤ 1%。"""
        model = SqrtImpactSlippage(eta=1.0, sigma=1.0, impact_cap=0.01)
        assert model.impact_of(1_000_000, 1.0) == 0.01
        price = model.apply(_order(qty=1_000_000), 10.0, _state(),
                            _bar(volume=1.0))
        assert price == pytest.approx(10.1, rel=1e-12)

    def test_sqrt_scaling_law(self):
        """规模相关：4× 委托量 ⇒ 2× 冲击（平方根律——不是线性）。"""
        model = SqrtImpactSlippage()
        assert model.impact_of(400_000, 1_000_000) == pytest.approx(
            2 * model.impact_of(100_000, 1_000_000), rel=1e-12)

    def test_boundaries_no_impact(self):
        """无 bar / 量 ≤0 / 委托 ≤0 ⇒ 不施加（返回参考价）。"""
        model = SqrtImpactSlippage()
        assert model.apply(_order(), 10.0, _state(), None) == 10.0
        assert model.apply(_order(), 10.0, _state(), _bar(volume=0.0)) == 10.0
        assert model.apply(_order(qty=0), 10.0, _state(), _bar()) == 10.0

    def test_bad_params_rejected(self):
        with pytest.raises(ValueError, match="sqrt_impact"):
            SqrtImpactSlippage(eta=-0.1)
        with pytest.raises(ValueError, match="sqrt_impact"):
            SqrtImpactSlippage(sigma=-0.01)
        with pytest.raises(ValueError, match="sqrt_impact"):
            SqrtImpactSlippage(impact_cap=0.0)


class TestRegistrationAndWiring:
    def test_registered_name(self):
        assert "sqrt_impact" in registry.available(registry.SLIPPAGE_MODEL)
        model = registry.create(registry.SLIPPAGE_MODEL, "sqrt_impact",
                                {"eta": 0.2, "sigma": 0.03,
                                 "impact_cap": 0.02})
        assert isinstance(model, SqrtImpactSlippage)
        assert (model.eta, model.sigma, model.impact_cap) == (0.2, 0.03, 0.02)

    def test_default_still_pct(self):
        """**默认不启用**：缺省滑点仍 `pct 0.001`（RC-01）——本模型须显式声明。"""
        from btf.config.loader import DEFAULTS

        assert DEFAULTS["execution"]["slippage"] == {
            "model": "pct", "params": {"pct": 0.001}}

    def test_rendered_in_report_assumptions(self):
        """报告假设串：η/σ/cap 三参数全部可见（研究参数必须可追溯）。"""
        from btf.viz.report import format_slippage

        text = format_slippage("sqrt_impact",
                               {"eta": 0.5, "sigma": 0.1, "impact_cap": 0.02})
        assert "sqrt_impact" in text
        assert "η=0.5" in text and "σ=0.1" in text and "cap=200bp" in text
        assert "研究参数" in text
        # 缺省参数（未声明）也须给出可读串（不得只剩模型名）
        assert "η=0.1" in format_slippage("sqrt_impact")

    def test_runtime_wiring_end_to_end(self):
        """端到端：配置 `sqrt_impact` ⇒ 装配即该模型，成交价 = 开盘 ×(1+冲击)。"""
        from btf.runtime import BTFRuntime

        from tests.fixtures.scenarios import t_plus1_scenario

        feed = t_plus1_scenario().feed

        def factory(**_params):
            return feed

        factory.contract_version = CONTRACT_VERSION
        if FEED_NAME not in registry.available(registry.DATA_FEED):
            registry.register(registry.DATA_FEED, FEED_NAME, factory)

        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "000001.SZ", "weight": 0.9},
                "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
                "period": {"start": "2015-01-05", "end": "2015-01-09"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": FEED_NAME},
            "risk": {"rules": [], "allow_empty_chain": True},
            "execution": {"slippage": {"model": "sqrt_impact",
                                       "params": {"eta": 0.5, "sigma": 0.1}}},
        }
        rt = BTFRuntime().load_config(cfg).build()
        assert isinstance(rt.slippage, SqrtImpactSlippage)
        assert (rt.slippage.eta, rt.slippage.sigma) == (0.5, 0.1)
        result = rt.run(persist=False)
        assert result.fills, "场景应有成交"
        fill = result.fills[0]
        order = next(o for o in result.orders if o.order_id == fill.order_id)
        # D2 开盘 = 10.1（t_plus1 场景 stock_px[1]）；冲击按**委托量**计
        expected = 10.1 * (1 + rt.slippage.impact_of(order.qty, 1_000_000.0))
        assert fill.price == pytest.approx(expected, rel=1e-12)
        assert fill.price > 10.1                     # 买入冲击为正
