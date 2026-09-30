# -*- coding: utf-8 -*-
"""事件溯源日志：JSONL 编解码 + 落盘（采样预算）+ 回放器骨架（08 §13.6）。

编解码职责切分（domain 零依赖纪律）：
    - domain/events.py 只冻结类型（不 import json）；
    - 本模块持 codec：事件 ↔ JSON 行。市场事件的 data 截面**只落摘要**
      （sorted symbol 列表；08 §13.6 "类型+载荷摘要"），回放语义=时序与
      订单流审计，不要求重建全市场截面。

采样预算（v0.2 修订 S3）：
    - 默认全量落盘，超过 max_records（默认 50 万，可配 events.max_records）
      自动降级采样模式：每 stride 事件落 1 条 + 首/尾 1000 条全保
      （首段全量天然保留；尾部经 ring buffer 在 close() 补写，去重）；
    - 降级时写哨兵行（kind=_sampled），reader 据此提示 replay 不完整；
    - 确定性：采样判定只依赖事件序号（seq % stride），与事件内容无关。
"""
from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

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

#: 采样哨兵行类型标签（非事件；reader 跳过并登记降级状态）
_SAMPLED_MARK = "_sampled"


# ─────────────────────────────────────────────
# 编码：事件 → dict payload
# ─────────────────────────────────────────────
def _d(td: TradingDate | None) -> str | None:
    return None if td is None else td.to_ymd()


def _enc_order(o: Order) -> dict[str, Any]:
    return {
        "order_id": o.order_id, "symbol": o.symbol,
        "side": o.side.value, "order_type": o.order_type.value,
        "qty": o.qty, "limit_price": o.limit_price,
        "created_at": _d(o.created_at), "tif": o.tif, "tag": o.tag,
    }


def _enc_fee(f: Fee) -> dict[str, float]:
    return asdict(f)


def _enc_fill(f: Fill) -> dict[str, Any]:
    return {
        "fill_id": f.fill_id, "order_id": f.order_id, "symbol": f.symbol,
        "side": f.side.value, "qty": f.qty, "price": f.price,
        "fee": _enc_fee(f.fee), "fill_date": _d(f.fill_date),
        "fill_timing": f.fill_timing,
    }


def _enc_rejection(r: Rejection) -> dict[str, Any]:
    return {"code": r.code.value, "message": r.message}


def _enc_action(a: CorporateAction) -> dict[str, Any]:
    return {
        "symbol": a.symbol, "ex_date": _d(a.ex_date), "pay_date": _d(a.pay_date),
        "record_date": _d(a.record_date),
        "cash_div_per_share": a.cash_div_per_share,
        "stk_div_per_share": a.stk_div_per_share, "ann_date": _d(a.ann_date),
    }


def encode_event(event: EngineEvent) -> dict[str, Any]:
    """事件 → {"kind": …, "payload": …}。市场事件 data 落**计数摘要**
    （n_symbols；全量 4000 symbol 数组的 JSON 序列化是 B2 实测 ~7s 热点，
    时序审计语义计数足够；逐笔订单流才是回放核心）。"""
    if isinstance(event, MarketOpenEvent | MarketCloseEvent):
        payload: dict[str, Any] = {"date": _d(event.date), "n_symbols": len(event.data)}
    elif isinstance(event, OrderSubmittedEvent):
        payload = {"order": _enc_order(event.order)}
    elif isinstance(event, OrderRejectedEvent):
        payload = {"order": _enc_order(event.order), "reason": _enc_rejection(event.reason)}
    elif isinstance(event, FillEvent):
        payload = {"fill": _enc_fill(event.fill)}
    elif isinstance(event, CorporateActionEvent):
        payload = {"action": _enc_action(event.action)}
    elif isinstance(event, SessionEndEvent):
        payload = {"date": _d(event.date)}
    else:
        raise TypeError(f"未登记事件类型: {type(event).__name__}")
    return {"kind": _KIND_OF[type(event)], "payload": payload}


#: 事件类 → 类型标签（EVENT_KINDS 的反向映射，模块加载期一次构建）
_KIND_OF: dict[type, str] = {cls: k for k, cls in EVENT_KINDS.items()}


# ─────────────────────────────────────────────
# 解码：dict payload → 事件
# ─────────────────────────────────────────────
def _td(s: str | None) -> TradingDate | None:
    return None if s is None else TradingDate.from_ymd(s)


def _dec_order(p: dict[str, Any]) -> Order:
    return Order(
        order_id=p["order_id"], symbol=p["symbol"],
        side=OrderSide(p["side"]), order_type=OrderType(p["order_type"]),
        qty=p["qty"], limit_price=p["limit_price"],
        created_at=TradingDate.from_ymd(p["created_at"]),
        tif=p.get("tif", "day"), tag=p.get("tag"),
    )


def _dec_fee(p: dict[str, Any]) -> Fee:
    return Fee(**p)


def _dec_fill(p: dict[str, Any]) -> Fill:
    return Fill(
        fill_id=p["fill_id"], order_id=p["order_id"], symbol=p["symbol"],
        side=OrderSide(p["side"]), qty=p["qty"], price=p["price"],
        fee=_dec_fee(p["fee"]),
        fill_date=TradingDate.from_ymd(p["fill_date"]),
        fill_timing=p["fill_timing"],
    )


def _dec_rejection(p: dict[str, Any]) -> Rejection:
    return Rejection(code=RejectCode(p["code"]), message=p.get("message", ""))


def _dec_action(p: dict[str, Any]) -> CorporateAction:
    return CorporateAction(
        symbol=p["symbol"],
        ex_date=TradingDate.from_ymd(p["ex_date"]), pay_date=_td(p["pay_date"]),
        record_date=_td(p["record_date"]),
        cash_div_per_share=p["cash_div_per_share"],
        stk_div_per_share=p["stk_div_per_share"], ann_date=_td(p["ann_date"]),
    )


def decode_event(obj: dict[str, Any]) -> EngineEvent:
    """{"kind": …, "payload": …} → 事件。市场事件 data 恢复为空 map（摘要语义）。"""
    kind, p = obj["kind"], obj["payload"]
    if kind in ("market_open", "market_close"):
        cls = MarketOpenEvent if kind == "market_open" else MarketCloseEvent
        return cls(date=TradingDate.from_ymd(p["date"]), data={})
    if kind == "order_submitted":
        return OrderSubmittedEvent(order=_dec_order(p["order"]))
    if kind == "order_rejected":
        return OrderRejectedEvent(order=_dec_order(p["order"]), reason=_dec_rejection(p["reason"]))
    if kind == "fill":
        return FillEvent(fill=_dec_fill(p["fill"]))
    if kind == "corporate_action":
        return CorporateActionEvent(action=_dec_action(p["action"]))
    if kind == "session_end":
        return SessionEndEvent(date=TradingDate.from_ymd(p["date"]))
    raise ValueError(f"未知事件 kind: {kind!r}")


# ─────────────────────────────────────────────
# 落盘：全量 → 超限采样（stride 抽 1 + 尾部 1000 全保）
# ─────────────────────────────────────────────
class EventLogWriter:
    """events.jsonl 落盘器（含采样降级；close 前数据在 OS 缓冲，崩溃可审计性由 M2 RunStore 原子写收口）。"""

    def __init__(
        self,
        path: Path | str,
        *,
        max_records: int = 500_000,
        sample_stride: int = 10,
        tail_keep: int = 1000,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._max_records = max_records
        self._stride = max(2, sample_stride)
        self._tail_keep = tail_keep
        self._fh: TextIO = self.path.open("w", encoding="utf-8", newline="\n")
        self._seq = 0                # 已见事件数（采样判定基准）
        #: 尾部 ring 存**事件对象**（PF-7：延迟编码——超预算行不再立即 dumps）
        self._tail: deque[tuple[int, EngineEvent]] = deque(maxlen=tail_keep)
        self.sampled = False         # 降级标志（报告披露 "事件日志已采样"）
        #: 实际 `json.dumps` 次数（PF-7 断言锚点；铁律新 16：不恒空）
        self.n_serialized = 0

    @property
    def n_events(self) -> int:
        return self._seq

    def _encode(self, seq: int, event: EngineEvent) -> str:
        self.n_serialized += 1
        return json.dumps(
            {"seq": seq, **encode_event(event)},
            ensure_ascii=False, sort_keys=True,
        )

    def write(self, event: EngineEvent) -> None:
        """写一个事件；超预算自动降级采样（每 stride 条落 1 + 尾部补写）。

        PF-7 / P2-2（19 号附录 D.3）：**预算判断前置**到 `json.dumps` 之前。
        原实现"先序列化、再判预算"——采样模式下每行仍全量 `dumps`（省盘不省
        CPU；报告实测把 `{{ title }}` 类大事件串在采样后仍全量编码）。
        今：尾部 ring 改存**事件对象**（≤ `tail_keep` 个），只在两种情形
        序列化——① 当期就落盘的行（预算内 / stride 命中）；② `close()` 时
        真正要补写的存活行。**落盘行集逐行不变**（同一 `_encode` 口径）。
        另累计 `n_serialized`（PF-7 断言锚点；**不得恒空**——铁律新 16）。
        """
        self._seq += 1
        seq = self._seq
        if seq <= self._max_records:
            self._fh.write(self._encode(seq, event) + "\n")
            self._tail.append((seq, event))
            return
        if not self.sampled:
            self.sampled = True
            self._fh.write(json.dumps(
                {"seq": seq, "kind": _SAMPLED_MARK, "stride": self._stride},
                ensure_ascii=False, sort_keys=True,
            ) + "\n")
        # 采样模式：seq % stride == 0 才序列化落盘；其余仅入尾部 ring（延迟编码）
        if seq % self._stride == 0:
            self._fh.write(self._encode(seq, event) + "\n")
        self._tail.append((seq, event))
        if seq % 4096 == 0:              # 阈值 flush：崩溃丢尾部上限可控
            self._fh.flush()

    def close(self) -> None:
        """补写尾部全保段（仅采样段中未落盘的行），幂等。"""
        if self._fh.closed:
            return
        for seq, event in self._tail:
            if seq <= self._max_records:
                continue  # 全量段已在 write() 落盘
            if seq % self._stride == 0:
                continue  # 采样流已落盘，跳过去重
            self._fh.write(self._encode(seq, event) + "\n")
        self._fh.close()

    def __enter__(self) -> EventLogWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ─────────────────────────────────────────────
# 回放器骨架：逐行读（bt replay 的读取端）
# ─────────────────────────────────────────────
class EventLogReader:
    """events.jsonl 回放器（逐行 decode；遇采样哨兵标记 replay 不完整）。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.sampled = False

    def events(self) -> Iterator[EngineEvent]:
        """逐行产出事件（采样日志：产出为采样后的子序列，`self.sampled`=True）。"""
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("kind") == _SAMPLED_MARK:
                    self.sampled = True
                    continue
                yield decode_event(obj)


__all__ = [
    "EventLogReader",
    "EventLogWriter",
    "decode_event",
    "encode_event",
]
