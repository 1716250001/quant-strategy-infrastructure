# -*- coding: utf-8 -*-
"""MemoryFeed：测试专用内存 Feed（PoC-2 任务 2.2，04 §8.3.1 协议子集实现）。

用途：引擎/撮合/记账单测的手写小数据集载体（3 场景见 tests/fixtures/scenarios.py）
与 B2 基准的 MemoryFeed→真实 Feed 对照路径。
纪律：同参数同结果、无副作用（协议要求）；仅内存数据，无 I/O。
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from btf.domain.action import CorporateAction
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.market import Bar, TradingState
from btf.domain.types import Instrument, TradingDate


class MemoryFeed:
    """手写数据集 Feed：bars 按标的分组传入；states 按 (ymd, symbol) 登记。

    corporate_actions：构造时注入行动列表（PoC-3 任务 3.2——G1 五案例/
    守恒断言/等价断言的手写行动流载体）；缺省空。
    """

    contract_version = CONTRACT_VERSION

    def __init__(
        self,
        bars: Sequence[Bar],
        states: Mapping[tuple[str, str], TradingState] | None = None,
        instruments: Mapping[str, Instrument] | None = None,
        dates: Sequence[TradingDate] | None = None,
        actions: Sequence[CorporateAction] | None = None,
    ):
        """dates：显式交易日历（升序）。停牌日仍在日历内但截面缺席——
        缺省取 bar 日期并集（无停牌场景等价）。"""
        self._by_symbol: dict[str, list[Bar]] = {}
        for b in bars:
            self._by_symbol.setdefault(b.symbol, []).append(b)
        for series in self._by_symbol.values():
            series.sort(key=lambda b: b.date)
        self._states = dict(states or {})
        self._instruments = dict(instruments or {})
        self._actions = sorted(actions or [], key=lambda a: (a.ex_date, a.symbol))
        # 交易日域：显式日历优先（停牌日保留）；否则 bar 日期并集
        if dates is not None:
            self._dates: list[TradingDate] = list(dates)
            for a, b in zip(self._dates, self._dates[1:], strict=False):
                if not a < b:
                    raise ValueError(f"交易日历非升序: {a} !< {b}")
        else:
            self._dates = sorted({b.date for b in bars})

    # ── DataFeed 协议 ──
    def universe(self, date: TradingDate) -> list[Instrument]:
        """构造时注入的 instruments，按上市/退市窗口过滤（静态身份）。"""
        return self._universe_where(
            lambda inst: not (inst.list_date and inst.list_date > date)
            and not (inst.delist_date and inst.delist_date < date))

    def universe_span(self, start: TradingDate,
                      end: TradingDate) -> list[Instrument]:
        """[start, end] 期间并集（P2-NEW-3，19 号 §22.3）。

        语义与 `universe` 同谓词闭式：`list_date ≤ end ∧ (delist_date 空 ∨
        delist_date ≥ start)`——本 Feed 为**全内存**注入，故该并集是**精确**的
        （不像 parquet 回退路径会漏"期间上市且期间退市"的标的）。
        """
        return self._universe_where(
            lambda inst: not (inst.list_date and inst.list_date > end)
            and not (inst.delist_date and inst.delist_date < start))

    def _universe_where(self, predicate) -> list[Instrument]:
        out = [inst for inst in self._instruments.values() if predicate(inst)]
        return sorted(out, key=lambda i: i.symbol)

    def bars_of(self, symbol: str, start: TradingDate, end: TradingDate) -> list[Bar]:
        return [b for b in self._by_symbol.get(symbol, []) if start <= b.date <= end]

    def history(self, symbol: str, end: TradingDate,
                fields: Sequence[str] | None = None, n_bars: int = 1) -> list[Bar]:
        """截至 end 最近 n_bars 个 bar（协议口径，04 §8.3.1；全字段）。"""
        bars = self.bars_of(symbol, TradingDate.from_ymd("19000101"), end)
        return bars[-n_bars:] if n_bars > 0 else []

    def bars(
        self,
        symbols: Sequence[str] | None,
        start: TradingDate,
        end: TradingDate,
    ) -> Iterator[tuple[TradingDate, Mapping[str, Bar]]]:
        """按交易日迭代截面（日期升序；停牌股当日缺席 map——语义与真实 Feed 一致）。"""
        wanted = set(symbols) if symbols is not None else None
        for d in self._dates:
            if not (start <= d <= end):
                continue
            section: dict[str, Bar] = {}
            for sym, series in self._by_symbol.items():
                if wanted is not None and sym not in wanted:
                    continue
                for b in series:            # 短序列线性扫（手写小数据集语义）
                    if b.date == d:
                        section[sym] = b
                        break
                    if b.date > d:
                        break
            yield d, section

    def trading_states(
        self, date: TradingDate, symbols: Sequence[str] | None = None,
        *, with_touch_flags: bool = True,
    ) -> Mapping[str, TradingState]:
        """with_touch_flags：MemoryFeed 手写状态自带标记，参数仅为协议对齐。"""
        ymd = date.to_ymd()
        wanted = set(symbols) if symbols is not None else None
        return {
            sym: st
            for (d, sym), st in self._states.items()
            if d == ymd and (wanted is None or sym in wanted)
        }

    def corporate_actions(
        self, start: TradingDate, end: TradingDate
    ) -> list[CorporateAction]:
        """区间行动流（升序确定性；签名=DataFeed 协议批量口径）。"""
        return [a for a in self._actions if start <= a.ex_date <= end]

    # ── 测试便捷 ──
    def instrument(self, symbol: str) -> Instrument | None:
        return self._instruments.get(symbol)

    @property
    def dates(self) -> list[TradingDate]:
        return list(self._dates)

    @property
    def bars_all(self) -> list[Bar]:
        """全部 bar（场景合并/断言用，只读副本）。"""
        return [b for s in self._by_symbol.values() for b in s]

    @property
    def states_all(self) -> dict[tuple[str, str], TradingState]:
        """全部状态行（(ymd, symbol) → state，只读副本）。"""
        return dict(self._states)

    def add_state(self, state: TradingState) -> None:
        """登记单个状态面板行（场景构造用）。"""
        self._states[(state.date.to_ymd(), state.symbol)] = state


__all__ = ["MemoryFeed"]
