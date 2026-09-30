# -*- coding: utf-8 -*-
"""模拟时钟（SimClock，06 §10.2：回测/实时同一 Engine 内核，仅换时钟源）。

职责：唯一"当前日"真源——引擎每推进一个交易日调用 advance；
ctx 前视断言（end > current 即触发）依赖它，禁止策略侧持有独立日期推进。
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from itertools import pairwise

from btf.domain.types import TradingDate


class SimClock:
    """回测时钟：日期序列由外部（Feed 交易日流）驱动推进。"""

    def __init__(self) -> None:
        self._current: TradingDate | None = None
        self._n_days = 0

    @property
    def current(self) -> TradingDate | None:
        """当前交易日（引擎循环内 = T；None = 尚未开始）。"""
        return self._current

    @property
    def n_days(self) -> int:
        return self._n_days

    def advance(self, date: TradingDate) -> TradingDate:
        """推进到下一交易日（必须严格升序，违例=时序逻辑破坏，立即失败）。"""
        if self._current is not None and date <= self._current:
            raise RuntimeError(
                f"SimClock 时序违例: {date} <= 当前 {self._current}（日期必须严格升序）"
            )
        self._current = date
        self._n_days += 1
        return date

    def __iter__(self) -> Iterator[TradingDate]:
        return iter(())  # 迭代驱动在 Feed（bars 日期流），时钟只记账

    @staticmethod
    def of(dates: Sequence[TradingDate]) -> list[TradingDate]:
        """交易日序列校验（升序去重）——供构造 Feed 日期域时使用。"""
        out = list(dates)
        for a, b in pairwise(out):
            if not a < b:
                raise ValueError(f"交易日序列非升序: {a} !< {b}")
        return out


__all__ = ["SimClock"]
