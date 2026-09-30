# -*- coding: utf-8 -*-
"""L1 单测：事件类型与订单域冻结（PoC-2 任务 2.1 验收）。

验收锚点（18 号计划 2.1）：冻结 dataclass；事件 JSONL 序列化往返单测。
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from btf.domain.action import CorporateAction
from btf.domain.events import (
    EVENT_KINDS,
    CorporateActionEvent,
    EngineEvent,
    FillEvent,
    MarketCloseEvent,
    MarketOpenEvent,
    OrderRejectedEvent,
    OrderSubmittedEvent,
    SessionEndEvent,
)
from btf.domain.market import Bar
from btf.domain.orders import (
    Fee,
    Fill,
    Order,
    OrderSide,
    OrderType,
    RejectCode,
    Rejection,
)
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l1]

D = TradingDate.from_iso(2026, 9, 21)


def _bar(symbol: str = "000001.SZ", day: TradingDate = D) -> Bar:
    return Bar(symbol=symbol, date=day, open=10.0, high=11.0, low=9.5,
               close=10.5, volume=1_000.0, amount=10_500.0, pre_close=10.0)


def _order(order_id: str = "O00000001") -> Order:
    return Order(order_id=order_id, symbol="000001.SZ", side=OrderSide.BUY,
                 order_type=OrderType.MARKET, qty=100, limit_price=None, created_at=D)


def _fill(fill_id: str = "F00000001") -> Fill:
    return Fill(fill_id=fill_id, order_id="O00000001", symbol="000001.SZ",
                side=OrderSide.BUY, qty=100, price=10.02,
                fee=Fee(commission=5.0, stamp_duty=0.0, transfer_fee=0.1, total=5.1),
                fill_date=D, fill_timing="open")


class TestOrderDomain:
    """订单域冻结（04 §8.2.5 签名照抄）。"""

    def test_order_immutable(self):
        o = _order()
        with pytest.raises(FrozenInstanceError):
            o.qty = 200  # type: ignore[misc]

    def test_order_equality(self):
        assert _order() == _order()
        assert _order() != _order("O00000002")

    def test_fill_immutable_and_fee_fields(self):
        f = _fill()
        assert (f.fee.commission, f.fee.stamp_duty, f.fee.transfer_fee, f.fee.total) == (
            5.0, 0.0, 0.1, 5.1)
        with pytest.raises(FrozenInstanceError):
            f.price = 10.5  # type: ignore[misc]

    def test_fee_zero(self):
        z = Fee.zero()
        assert (z.commission, z.stamp_duty, z.transfer_fee, z.total) == (0.0, 0.0, 0.0, 0.0)

    def test_reject_code_eight(self):
        """拒单八 code 全集（18 号 2.4 六项 + M2 5.2 RISK_REJECTED +
        v0.5 V5-6 VOLUME_CAP——VPP 撮合量上限不足一手）。"""
        assert {c.name for c in RejectCode} == {
            "SUSPENDED", "LIMIT_UP", "LIMIT_DOWN",
            "T_PLUS_1", "LOT_SIZE", "VOLUME_CAP",
            "INSUFFICIENT_CASH", "RISK_REJECTED",
        }


class TestEventTypes:
    """七类事件冻结（04 §8.4）。"""

    def test_seven_kinds_registered(self):
        assert set(EVENT_KINDS) == {
            "market_open", "market_close", "order_submitted",
            "order_rejected", "fill", "corporate_action", "session_end",
        }
        assert len(EVENT_KINDS) == 7

    def test_all_events_frozen_and_subclass_base(self):
        evts = [
            MarketOpenEvent(date=D, data={"000001.SZ": _bar()}),
            MarketCloseEvent(date=D, data={}),
            OrderSubmittedEvent(order=_order()),
            OrderRejectedEvent(order=_order(), reason=Rejection(RejectCode.SUSPENDED, "停牌")),
            FillEvent(fill=_fill()),
            CorporateActionEvent(action=CorporateAction(
                symbol="000001.SZ", ex_date=D, pay_date=None, record_date=None,
                cash_div_per_share=0.1, stk_div_per_share=0.0, ann_date=None)),
            SessionEndEvent(date=D),
        ]
        for e in evts:
            assert isinstance(e, EngineEvent)
            with pytest.raises(FrozenInstanceError):
                e.date = D  # type: ignore[misc]

    def test_market_event_holds_lazy_mapping(self):
        """data 允许懒截面（Mapping 协议），不强制 dict。"""
        class _Lazy(dict):
            pass

        e = MarketOpenEvent(date=D, data=_Lazy())
        assert len(e.data) == 0
