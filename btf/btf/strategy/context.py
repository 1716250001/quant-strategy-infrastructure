# -*- coding: utf-8 -*-
"""StrategyContext：策略与引擎的唯一接口（04 §8.3.2；06 §8.1）。

分层纪律（03 §7.3）：strategy 层不 import engine/portfolio（外层）——
依赖面以 Protocol/Callable 声明，engine 组装时注入实现。

前视封堵（PoC-2 任务 2.5 验收）：history() 的 end 参数超过当前交易日
（now()）立即触发运行时断言——策略无法以任何形式访问未来 bar。
数据访问收敛：bar/history/state 只读视图；组合视图为当日已重估状态
（on_close 时点 = T 收盘后，非盘中前视）。

history 缓存：per-symbol 全区间一次装载（engine 生命周期），后续
history() 调用纯内存切片——B2 月调仓 10 年 50 标的 ≈ 50 次单标的读。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from btf.domain.cache import BoundedDict
from btf.domain.market import Bar, TradingState
from btf.domain.orders import Order
from btf.domain.types import Instrument, TradingDate
from btf.strategy.rebalance import TargetPortfolio


class PortfolioProbe(Protocol):
    """策略可见的组合视图（结构化类型；engine 注入 Portfolio 实例）。"""

    @property
    def cash(self) -> float: ...
    @property
    def positions(self) -> Mapping[str, Any]: ...
    def total_value(self) -> float: ...
    def position(self, symbol: str) -> Any | None: ...


class LazyStates(Mapping[str, TradingState]):
    """延迟物化的当日状态面板（B2 优化，任务 2.8 第二刀）。

    engine 每日构造 ctx 时只捕获 loader（date/标的集合已在构造时点
    固化），首次 state()/迭代访问才调用 feed.trading_states——月调仓
    策略不查状态时，逐日 (ST 区间线性扫描 + TradingState 合成) 全部
    豁免（2430 日量级）。语义等价：物化只依赖固化参数，不依赖访问
    时点的组合状态（on_open 构造后至 on_close 结束持仓不变）。
    """

    __slots__ = ("_built", "_loader")

    def __init__(self, loader: Callable[[], Mapping[str, TradingState]]):
        self._loader = loader
        self._built: Mapping[str, TradingState] | None = None

    def _resolve(self) -> Mapping[str, TradingState]:
        if self._built is None:
            self._built = self._loader()
        return self._built

    def __getitem__(self, key: str) -> TradingState:
        return self._resolve()[key]

    def get(self, key: str, default: TradingState | None = None):
        return self._resolve().get(key, default)

    def __iter__(self):
        return iter(self._resolve())

    def __len__(self) -> int:
        return len(self._resolve())

    def __contains__(self, key: object) -> bool:
        return key in self._resolve()


class StrategyContext:
    """引擎每日重建（data/states/当日日期），history 缓存跨日共享。"""

    def __init__(
        self,
        *,
        now: Callable[[], TradingDate | None],
        portfolio: PortfolioProbe,
        data: Mapping[str, Bar],
        states: Mapping[str, TradingState],
        history_loader: Callable[[str], Sequence[Bar]],
        on_submit_target: Callable[[TargetPortfolio], None],
        on_submit_order: Callable[[Order], None],
        start: TradingDate,
        end: TradingDate,
        universe_loader: Callable[[TradingDate], Sequence[Instrument]] | None = None,
    ):
        self._now = now
        self._portfolio = portfolio
        self._data = data
        self._states = states
        self._loader = history_loader
        # PF-9/P2-5：原无界 dict → LRU 有界（策略可见标的上限远小于 8k）
        self._cache: BoundedDict[str, Sequence[Bar]] = BoundedDict(
            maxsize=8_000, name="ctx.history")
        self._submit_target = on_submit_target
        self._submit_order = on_submit_order
        self.start = start
        self.end = end
        self._universe_loader = universe_loader

    # ── 数据访问（只读，前视封堵）──
    def universe(self) -> Sequence[Instrument]:
        """当日可交易宇宙（静态身份，04 §8.3.2；ST/停牌标记走 state()）。"""
        if self._universe_loader is None:
            raise RuntimeError(
                "本引擎装配未注入 universe_loader（DataFeed.universe 不可用）")
        current = self._now()
        if current is None:
            raise RuntimeError("宇宙查询早于首个交易日（now=None）")
        return self._universe_loader(current)
    def bar(self, symbol: str) -> Bar | None:
        """当日 bar（停牌缺席 → None）。"""
        return self._data.get(symbol)

    def history(self, symbol: str, end: TradingDate, fields: Sequence[str] | None = None,
                n_bars: int = 1) -> list[Bar]:
        """截至 end（含）的最近 n_bars 个 bar（升序）。

        前视封堵：end > 当前交易日 → RuntimeError（运行时断言，任务 2.5 验收）。
        fields 预留（列裁剪优化，PoC 全字段返回）。
        """
        current = self._now()
        if current is not None and end > current:
            raise RuntimeError(
                f"前视封堵：history end={end} 超过当前交易日 {current}"
            )
        bars = self._cache.get(symbol)
        if bars is None:
            bars = self._loader(symbol)
            self._cache[symbol] = bars
        tail = [b for b in bars if b.date <= end][-n_bars:] if n_bars > 0 else []
        return list(tail)

    def state(self, symbol: str) -> TradingState | None:
        """当日状态面板行（停牌/涨跌停/ST）。"""
        return self._states.get(symbol)

    # ── 组合视图（当日重估后状态）──
    @property
    def portfolio(self) -> PortfolioProbe:
        return self._portfolio

    @property
    def current_date(self) -> TradingDate | None:
        return self._now()

    # ── 决策提交 ──
    def submit_target(self, target: TargetPortfolio) -> None:
        """提交目标组合（⑦ Rebalancer 转订单；每 on_close 至多一次生效）。"""
        self._submit_target(target)

    def submit_order(self, order: Order) -> None:
        """显式订单（order_id 由引擎赋号；细粒度路径）。"""
        self._submit_order(order)


__all__ = ["LazyStates", "PortfolioProbe", "StrategyContext"]
