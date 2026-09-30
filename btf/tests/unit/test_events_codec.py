# -*- coding: utf-8 -*-
"""L1 单测：事件 JSONL 编解码往返 + 落盘采样预算 + 回放器（PoC-2 任务 2.1/2.6 验收）。

验收锚点：
    - 2.1 事件 JSONL 序列化往返单测；
    - 2.6 事件量级单测（50 万上限→采样降级+尾部全保+哨兵）；回放器骨架逐行读。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from btf.domain.action import CorporateAction
from btf.domain.events import (
    CorporateActionEvent,
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
from btf.engine.events_log import (
    EventLogReader,
    EventLogWriter,
    decode_event,
    encode_event,
)

pytestmark = [pytest.mark.l1]

D = TradingDate.from_iso(2026, 9, 21)
D2 = TradingDate.from_iso(2026, 9, 22)


def _bar(symbol: str, day: TradingDate) -> Bar:
    return Bar(symbol=symbol, date=day, open=10.0, high=11.0, low=9.5,
               close=10.5, volume=1_000.0, amount=10_500.0, pre_close=10.0)


def _order(order_id: str = "O00000001", side: OrderSide = OrderSide.BUY) -> Order:
    return Order(order_id=order_id, symbol="000001.SZ", side=side,
                 order_type=OrderType.MARKET, qty=100,
                 limit_price=None if side is OrderSide.BUY else 10.5,
                 created_at=D, tag="momentum")


def _fill(fill_id: str = "F00000001") -> Fill:
    return Fill(fill_id=fill_id, order_id="O00000001", symbol="000001.SZ",
                side=OrderSide.BUY, qty=100, price=10.02,
                fee=Fee(commission=5.0, stamp_duty=0.0, transfer_fee=0.1, total=5.1),
                fill_date=D2, fill_timing="open")


def _all_event_instances() -> list:
    return [
        MarketOpenEvent(date=D, data={"000001.SZ": _bar("000001.SZ", D),
                                      "600519.SH": _bar("600519.SH", D)}),
        MarketCloseEvent(date=D, data={}),
        OrderSubmittedEvent(order=_order()),
        OrderRejectedEvent(order=_order("O00000002", OrderSide.SELL),
                           reason=Rejection(RejectCode.LIMIT_UP, "开盘涨停买不进")),
        FillEvent(fill=_fill()),
        CorporateActionEvent(action=CorporateAction(
            symbol="000001.SZ", ex_date=D, pay_date=D2, record_date=D,
            cash_div_per_share=0.15, stk_div_per_share=0.3, ann_date=D)),
        SessionEndEvent(date=D2),
    ]


class TestRoundTrip:
    """编码 → 解码往返（完整载荷事件逐字段全等）。"""

    @pytest.mark.parametrize("event", _all_event_instances(), ids=lambda e: type(e).__name__)
    def test_roundtrip(self, event):
        decoded = decode_event(json.loads(json.dumps(encode_event(event))))
        assert type(decoded) is type(event)
        if isinstance(event, MarketOpenEvent | MarketCloseEvent):
            # 市场事件 data 为摘要语义：往返恢复 date；截面以 sorted symbols 表达
            assert decoded.date == event.date
            assert decoded.data == {}
        else:
            assert decoded == event

    def test_market_event_symbol_summary_sorted(self):
        e = MarketOpenEvent(date=D, data={"600519.SH": _bar("600519.SH", D),
                                          "000001.SZ": _bar("000001.SZ", D)})
        # B2 热点修正：摘要=计数（4000 symbol JSON 数组 ~7s/10 年）
        assert encode_event(e)["payload"]["n_symbols"] == 2

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError, match="未知事件 kind"):
            decode_event({"kind": "nonsense", "payload": {}})


class TestWriterReader:
    """落盘 + 回放（无采样路径）。"""

    def test_write_read_roundtrip(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        events = _all_event_instances()
        with EventLogWriter(path) as w:
            for e in events:
                w.write(e)
        assert w.sampled is False
        assert w.n_events == len(events)

        reader = EventLogReader(path)
        back = list(reader.events())
        assert reader.sampled is False
        assert len(back) == len(events)
        for orig, dec in zip(events, back, strict=True):
            assert type(dec) is type(orig)
            if not isinstance(orig, MarketOpenEvent | MarketCloseEvent):
                assert dec == orig

    def test_jsonl_one_event_per_line(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        with EventLogWriter(path) as w:
            w.write(SessionEndEvent(date=D))
            w.write(SessionEndEvent(date=D2))
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["seq"] == 1
        assert json.loads(lines[1])["seq"] == 2

    def test_sampled_degradation(self, tmp_path: Path):
        """事件量级单测：超上限 → 采样降级 + 尾部全保 + 哨兵 + reader 标记。"""
        path = tmp_path / "events.jsonl"
        n, max_rec, stride, tail = 300, 100, 10, 50
        with EventLogWriter(path, max_records=max_rec, sample_stride=stride,
                            tail_keep=tail) as w:
            for i in range(n):
                w.write(SessionEndEvent(date=TradingDate(date(2026, 1, 1 + i % 28))))
            assert w.sampled is True

        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").strip().split("\n")]
        kinds = [x["kind"] for x in lines]
        assert "_sampled" in kinds                       # 哨兵存在
        assert kinds.count("_sampled") == 1
        # 首段 1..100 全量 + 采样段 110,120,…,300（20 条）+ 尾部补写（51..300 未采样行）
        event_lines = [x for x in lines if x["kind"] != "_sampled"]
        seqs = [x["seq"] for x in event_lines]
        assert len(set(seqs)) == len(seqs)               # 无重复（close 去重）
        assert set(range(1, 101)) <= set(seqs)           # 首段全保
        assert set(range(251, 301)) <= set(seqs)         # 尾部全保
        sampled_in_band = {s for s in seqs if s > 100 and s <= 250 and s % stride == 0}
        assert {110, 120, 130, 140, 150} <= sampled_in_band  # 采样带：stride 抽 1

        reader = EventLogReader(path)
        back = list(reader.events())
        assert reader.sampled is True                    # replay 不完整提示依据
        assert len(back) == len(event_lines)

    def test_no_sampling_below_budget(self, tmp_path: Path):
        """预算内不降级：全量落盘（08 §13.6 默认全量）。"""
        path = tmp_path / "events.jsonl"
        with EventLogWriter(path, max_records=10) as w:
            for _ in range(10):
                w.write(SessionEndEvent(date=D))
        assert w.sampled is False
        assert len(path.read_text(encoding="utf-8").strip().split("\n")) == 10
