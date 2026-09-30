# -*- coding: utf-8 -*-
"""引擎事件类型体系（04 §8.4，QSTrader 四分类 + 强类型化，七类事件）。

冻结 dataclass（PoC-2 任务 2.1）：字段签名与 04 §8.4 逐条一致，
本文件一经冻结仅可经 ADR + S1 SemVer 流程变更（04 §8.7）。

事件流同时以 JSONL 落盘（事件溯源日志，08 §13.6）——编解码器在
engine/events_log.py（domain 零依赖纪律：不 import json）。

循环内事件序（06 §10.1 十步骤的事件投影，时序单测锚点）：
    CorporateActionEvent*（步骤①，PoC-3 接入）
    → MarketOpenEvent
    → FillEvent* / OrderRejectedEvent*（步骤②③撮合记账）
    → MarketCloseEvent（⑤重估后、⑥策略钩子前）
    → OrderSubmittedEvent*（步骤⑨入队）
    → SessionEndEvent（步骤⑩快照后，当日最后一事件）
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from btf.domain.action import CorporateAction
from btf.domain.market import Bar
from btf.domain.orders import Fill, Order, Rejection
from btf.domain.types import TradingDate


@dataclass(frozen=True)
class EngineEvent:
    """事件基类。子类全部 frozen dataclass（不可变=事件溯源可回放前提）。"""


@dataclass(frozen=True)
class MarketOpenEvent(EngineEvent):
    date: TradingDate
    data: Mapping[str, Bar]


@dataclass(frozen=True)
class MarketCloseEvent(EngineEvent):
    date: TradingDate
    data: Mapping[str, Bar]


@dataclass(frozen=True)
class OrderSubmittedEvent(EngineEvent):
    order: Order


@dataclass(frozen=True)
class OrderRejectedEvent(EngineEvent):
    order: Order
    reason: Rejection


@dataclass(frozen=True)
class FillEvent(EngineEvent):
    fill: Fill


@dataclass(frozen=True)
class CorporateActionEvent(EngineEvent):
    """公司行动事件（两时点分发语义见 domain/action.py）。"""

    action: CorporateAction


@dataclass(frozen=True)
class SessionEndEvent(EngineEvent):
    date: TradingDate    # 快照/费用结算时点


#: 类型标签 ↔ 事件类注册表（codec 单点映射；新事件=新文件+此处登记）
EVENT_KINDS: Mapping[str, type] = {
    "market_open": MarketOpenEvent,
    "market_close": MarketCloseEvent,
    "order_submitted": OrderSubmittedEvent,
    "order_rejected": OrderRejectedEvent,
    "fill": FillEvent,
    "corporate_action": CorporateActionEvent,
    "session_end": SessionEndEvent,
}


__all__ = [
    "EVENT_KINDS",
    "CorporateActionEvent",
    "EngineEvent",
    "FillEvent",
    "MarketCloseEvent",
    "MarketOpenEvent",
    "OrderRejectedEvent",
    "OrderSubmittedEvent",
    "SessionEndEvent",
]
