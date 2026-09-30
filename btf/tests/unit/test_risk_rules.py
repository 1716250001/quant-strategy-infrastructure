# -*- coding: utf-8 -*-
"""风控规则链单测（M2 任务 5.2；04 §8.3.4 三内置规则 + 链编排语义）。

覆盖：每规则 ALLOW/REDUCE/REJECT 三态、整手缩量、批次累计（on_batch_start）、
数据缺失降级、链序"首个非 ALLOW 生效"、batch_rejections 审计记录。
"""
from __future__ import annotations

import pytest
from btf.domain.market import TradingState
from btf.domain.orders import Fee, Fill, Order, OrderSide, OrderType
from btf.domain.types import TradingDate
from btf.portfolio.portfolio import Portfolio
from btf.registry import RISK_RULE, RegistryError, available, create, resolve
from btf.risk.manager import RiskVerdict, RuleChainManager, VerdictKind
from btf.risk.rules import CashCheckRule, MaxWeightRule, TradabilityRule

D = TradingDate.from_ymd


def _buy(qty: int, symbol: str = "000001.SZ") -> Order:
    return Order(order_id="", symbol=symbol, side=OrderSide.BUY,
                 order_type=OrderType.MARKET, qty=qty, limit_price=None,
                 created_at=D("20200106"))


def _sell(qty: int, symbol: str = "000001.SZ") -> Order:
    return Order(order_id="", symbol=symbol, side=OrderSide.SELL,
                 order_type=OrderType.MARKET, qty=qty, limit_price=None,
                 created_at=D("20200106"))


def _portfolio(cash: float = 1_000_000.0, *, mark: dict[str, float] | None = None
               ) -> Portfolio:
    p = Portfolio(cash=cash)
    if mark:
        p.mark_all(mark)
    return p


def _state(symbol: str = "000001.SZ", *, suspended: bool = False,
           delisted: bool = False, st: bool = False, limit_up: bool = False,
           limit_down: bool = False) -> TradingState:
    return TradingState(
        symbol=symbol, date=D("20200106"),
        limit_up_price=11.0 if limit_up else None,
        limit_down_price=9.0 if limit_down else None,
        is_suspended=suspended, is_st=st, is_delisted=delisted,
        is_limit_up=limit_up, is_limit_down=limit_down)


class TestMaxWeightRule:
    """max_weight：预算 = max_weight×NAV − 持仓市值；缩量向下整手。"""

    def test_allow_within_budget(self):
        rule = MaxWeightRule(max_weight=0.10)
        # NAV=100 万，预算 10 万；10 元买 5000 股=5 万 < 预算
        v = rule.check(_buy(5_000), _portfolio(mark={"000001.SZ": 10.0}), {})
        assert v.kind is VerdictKind.ALLOW

    def test_reduce_to_whole_lot(self):
        rule = MaxWeightRule(max_weight=0.10)
        # 预算 10 万；买 15000 股=15 万超限 → REDUCE_TO(10000)
        v = rule.check(_buy(15_000), _portfolio(mark={"000001.SZ": 10.0}), {})
        assert v.kind is VerdictKind.REDUCE and v.qty == 10_000

    def test_reduce_floors_to_lot_multiple(self):
        rule = MaxWeightRule(max_weight=0.05)
        # 预算 5 万 / 10 元 = 5000 股整；买 6000 → REDUCE_TO(5000)
        v = rule.check(_buy(6_000), _portfolio(mark={"000001.SZ": 10.0}), {})
        assert v.qty == 5_000

    def test_reject_when_holding_at_cap(self):
        rule = MaxWeightRule(max_weight=0.10)
        p = _portfolio(cash=900_000.0, mark={"000001.SZ": 10.0})
        # 已持 10000 股=10 万 = 10% 上限 → 预算 0 → REJECT
        # （apply_fill 后现金 80 万 + 持仓 10 万 = NAV 90 万；预算 9 万−10 万 < 0）
        p.apply_fill(Fill(
            fill_id="F1", order_id="O1", symbol="000001.SZ",
            side=OrderSide.BUY, qty=10_000, price=10.0, fee=Fee.zero(),
            fill_date=D("20200103"), fill_timing="open"))
        v = rule.check(_buy(1_000), p, {})
        assert v.kind is VerdictKind.REJECT and "max_weight" in v.reason_code

    def test_sell_always_allow(self):
        rule = MaxWeightRule(max_weight=0.01)
        v = rule.check(_sell(10_000), _portfolio(mark={"000001.SZ": 10.0}), {})
        assert v.kind is VerdictKind.ALLOW

    def test_missing_price_degrades_to_allow(self):
        """降级放行（历史口径）须**显式** `strict=False` 才生效。"""
        rule = MaxWeightRule(max_weight=0.10, strict=False)
        v = rule.check(_buy(1_000_000), _portfolio(), {})   # 无盯市价
        assert v.kind is VerdictKind.ALLOW

    def test_missing_price_rejects_by_default(self):
        """X-7（Q1=A）：默认 fail-closed——无盯市价即拒单（不再静默放行）。"""
        rule = MaxWeightRule(max_weight=0.10)
        v = rule.check(_buy(1_000), _portfolio(), {})
        assert v.kind is VerdictKind.REJECT and "max_weight" in v.reason_code

    def test_invalid_param(self):
        with pytest.raises(ValueError):
            MaxWeightRule(max_weight=0)


class TestCashCheckRule:
    """cash_check：批次内逐单扣减；不足缩整手或拒；钩子重置。"""

    def test_batch_cumulative(self):
        rule = CashCheckRule(fee_buffer=0.0)
        p = _portfolio(cash=100_000.0, mark={"000001.SZ": 10.0})
        rule.on_batch_start(p)
        assert rule.check(_buy(6_000), p, {}).kind is VerdictKind.ALLOW
        # 第二单仅剩 4 万：买 5000 → REDUCE_TO(4000)
        v = rule.check(_buy(5_000), p, {})
        assert v.kind is VerdictKind.REDUCE and v.qty == 4_000

    def test_on_batch_start_resets(self):
        rule = CashCheckRule(fee_buffer=0.0)
        p = _portfolio(cash=100_000.0, mark={"000001.SZ": 10.0})
        rule.on_batch_start(p)
        rule.check(_buy(6_000), p, {})          # 耗 6 万
        rule.on_batch_start(p)                  # 新批次重置
        assert rule.check(_buy(6_000), p, {}).kind is VerdictKind.ALLOW

    def test_reject_when_below_one_lot(self):
        rule = CashCheckRule(fee_buffer=0.0)
        p = _portfolio(cash=500.0, mark={"000001.SZ": 10.0})
        rule.on_batch_start(p)
        v = rule.check(_buy(1_000), p, {})
        assert v.kind is VerdictKind.REJECT and "cash" in v.reason_code

    def test_fee_buffer_counted(self):
        rule = CashCheckRule(fee_buffer=0.001)
        p = _portfolio(cash=100_000.0, mark={"000001.SZ": 10.0})
        rule.on_batch_start(p)
        # 单价 10×1.001=10.01；10 万 / 10.01 = 9990.009 → 整手 9900
        v = rule.check(_buy(10_000), p, {})
        assert v.kind is VerdictKind.REDUCE and v.qty == 9_900

    def test_sell_allow_no_refund(self):
        rule = CashCheckRule(fee_buffer=0.0)
        p = _portfolio(cash=0.0, mark={"000001.SZ": 10.0})
        rule.on_batch_start(p)
        assert rule.check(_sell(1_000), p, {}).kind is VerdictKind.ALLOW


class TestTradabilityRule:
    """tradability：停牌/退市/涨跌停/ST（可选）预拒；缺数据降级。"""

    def test_suspended_reject(self):
        v = TradabilityRule().check(_buy(100), _portfolio(),
                                    {"000001.SZ": _state(suspended=True)})
        assert v.kind is VerdictKind.REJECT and v.reason_code == "risk:suspended"

    def test_delisted_reject(self):
        v = TradabilityRule().check(_buy(100), _portfolio(),
                                    {"000001.SZ": _state(delisted=True)})
        assert v.kind is VerdictKind.REJECT and v.reason_code == "risk:delisted"

    def test_limit_up_blocks_buy_only(self):
        s = {"000001.SZ": _state(limit_up=True)}
        assert TradabilityRule().check(
            _buy(100), _portfolio(), s).kind is VerdictKind.REJECT
        assert TradabilityRule().check(
            _sell(100), _portfolio(), s).kind is VerdictKind.ALLOW

    def test_limit_down_blocks_sell_only(self):
        s = {"000001.SZ": _state(limit_down=True)}
        assert TradabilityRule().check(
            _sell(100), _portfolio(), s).kind is VerdictKind.REJECT
        assert TradabilityRule().check(
            _buy(100), _portfolio(), s).kind is VerdictKind.ALLOW

    def test_st_default_allow_reject_when_configured(self):
        s = {"000001.SZ": _state(st=True)}
        assert TradabilityRule().check(
            _buy(100), _portfolio(), s).kind is VerdictKind.ALLOW
        assert TradabilityRule(reject_st=True).check(
            _buy(100), _portfolio(), s).kind is VerdictKind.REJECT

    def test_missing_state_degrades(self):
        """降级放行（历史口径）须**显式** `strict=False` 才生效。"""
        v = TradabilityRule(strict=False).check(_buy(100), _portfolio(), {})
        assert v.kind is VerdictKind.ALLOW

    def test_missing_state_rejects_by_default(self):
        """X-7（Q1=A）：默认 fail-closed——无状态面板即拒单。"""
        v = TradabilityRule().check(_buy(100), _portfolio(), {})
        assert v.kind is VerdictKind.REJECT and "tradability" in v.reason_code


class _AllowAll:
    contract_version = "1.0"

    def check(self, order, portfolio, states):
        return RiskVerdict.allow()


class _AlwaysReduce:
    """哨兵：恒缩量到 700（奇数——验证后续规则不再干预）。"""

    def check(self, order, portfolio, states):
        return RiskVerdict.reduce_to(700)

    def on_batch_start(self, portfolio):
        self.called = True


class TestRuleChainManager:
    """链编排：首个非 ALLOW 生效；批次钩子；否决审计。"""

    def test_first_non_allow_wins_in_order(self):
        chain = RuleChainManager([_AllowAll(), _AlwaysReduce()])
        out = chain.pre_check([_buy(1_000)], _portfolio(), {})
        assert out[0].qty == 700                       # 缩量生效

    def test_batch_hook_called_per_pre_check(self):
        sentinel = _AlwaysReduce()
        chain = RuleChainManager([sentinel])
        chain.pre_check([_buy(100)], _portfolio(), {})
        assert sentinel.called is True

    def test_rejections_recorded_with_reason(self):
        chain = RuleChainManager([TradabilityRule()])
        out = chain.pre_check(
            [_buy(100)], _portfolio(), {"000001.SZ": _state(suspended=True)})
        assert out == []
        assert len(chain.batch_rejections) == 1
        order, reason = chain.batch_rejections[0]
        assert order.qty == 100 and "risk:suspended" in reason

    def test_reduce_to_zero_rejected_and_recorded(self):
        # NAV=100 万，max_weight=0.00005 → 预算 50 元 < 一手 1000 元 → 剔除
        chain = RuleChainManager([MaxWeightRule(max_weight=0.00005)])
        out = chain.pre_check([_buy(1_000)],
                              _portfolio(mark={"000001.SZ": 10.0}), {})
        assert out == []                                # 一手都装不下 → 剔除
        assert len(chain.batch_rejections) == 1

    def test_empty_chain_passes_with_warning(self, caplog):
        chain = RuleChainManager([])
        with caplog.at_level("WARNING"):
            out = chain.pre_check([_buy(100)], _portfolio(), {})
        assert len(out) == 1
        assert any("空" in r.message for r in caplog.records)


class TestRegistryRiskRules:
    """registry 声明制：三规则名可解析、协商、带参实例化。"""

    def test_names_registered(self):
        assert set(available(RISK_RULE)) >= {
            "max_weight", "cash_check", "tradability"}

    def test_create_with_params(self):
        r = create(RISK_RULE, "max_weight", {"max_weight": 0.05})
        assert isinstance(r, MaxWeightRule) and r.max_weight == 0.05
        r2 = create(RISK_RULE, "cash_check", {"fee_buffer": 0.0})
        assert r2.fee_buffer == 0.0

    def test_contract_version_negotiated(self):
        cls = resolve(RISK_RULE, "tradability")
        assert cls.contract_version == "1.0"

    def test_unknown_rule_name(self):
        with pytest.raises(RegistryError, match="未知插件名"):
            resolve(RISK_RULE, "position_limit")


@pytest.mark.l2
class TestEngineWiring:
    """引擎 ⑧ 步接线（06 §10.2 时序）：纵切场景风控缩量/否决生效。

    场景（t_plus1_scenario）：TEST 收盘 10.0 → T+1 开盘 10.1；
    buyhold weight=0.9 → 买单 90000 股；max_weight=0.5 → 预算 50 万
    → 缩量 50000 股（盯市价 10.0 口径）。
    """

    def test_max_weight_reduces_engine_order(self):
        from btf.engine.loop import Engine

        from tests.fixtures.buyhold import ConfigBuyHold
        from tests.fixtures.scenarios import t_plus1_scenario

        scen = t_plus1_scenario()
        engine = Engine(
            scen.feed, ConfigBuyHold(symbol="000001.SZ", weight=0.9),
            start=D("20150105"), end=D("20150109"),
            initial_cash=1_000_000.0,
            instruments=scen.instruments,
            risk_manager=RuleChainManager([MaxWeightRule(max_weight=0.5)]),
        )
        result = engine.run()
        assert len(result.fills) == 1
        assert result.fills[0].qty == 50_000           # 0.5×1M/10.0（整手）
        assert result.fills[0].price == pytest.approx(10.1)
        assert result.rejections == []                  # 缩量非否决

    def test_tradability_rejects_into_rejection_stream(self):
        """tradability 否决 → Rejection(code=risk_rejected) 进审计流。"""
        from btf.domain.orders import RejectCode
        from btf.engine.loop import Engine

        from tests.fixtures.buyhold import ConfigBuyHold
        from tests.fixtures.scenarios import t_plus1_scenario

        scen = t_plus1_scenario()
        engine = Engine(
            scen.feed, ConfigBuyHold(symbol="000001.SZ", weight=0.9),
            start=D("20150105"), end=D("20150109"),
            initial_cash=1_000_000.0,
            instruments=scen.instruments,
            risk_manager=RuleChainManager([TradabilityRule()]),
        )
        result = engine.run()
        # scenario 标的正常可交易：无否决（守护既有行为不被误伤）
        if result.rejections:
            assert all(r.code != RejectCode.RISK_REJECTED
                       for _, r in result.rejections)
