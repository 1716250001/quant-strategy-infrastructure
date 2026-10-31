# -*- coding: utf-8 -*-
"""L1：`ctx.history` 共享缓存 + bisect（19 号 §194 Z 转正，T-1~T-5）。

覆盖：跨 bar 命中（T-1）、bisect 三边界（T-2~T-4）、`n_bars<=0`（T-5）。
两侧语义等价已由 §192 Z-3 逐位比对（a1 六文件 SHA256 全同）保证。
"""
from __future__ import annotations

import pytest
from btf.domain.cache import BoundedDict
from btf.domain.types import TradingDate
from btf.strategy.context import StrategyContext

pytestmark = [pytest.mark.l1]

D1 = TradingDate.from_iso(2015, 1, 5)
D2 = TradingDate.from_iso(2015, 1, 6)
D3 = TradingDate.from_iso(2015, 1, 7)


class _Bar:
    """最小 bar 替身（只需 `.date`；`history` 只按日期定位）。"""

    __slots__ = ("date",)

    def __init__(self, d: TradingDate):
        self.date = d

    def __repr__(self) -> str:
        return f"B({self.date.to_ymd()})"


def _ctx(loader, *, cache=None, start=D1, end=D3, now=None):
    return StrategyContext(
        now=(lambda: now) if now is not None else (lambda: None),
        portfolio=None, data={}, states={},
        history_loader=loader,
        on_submit_target=lambda t: None,
        on_submit_order=lambda o: None,
        start=start, end=end,
        history_cache=cache,
    )


def test_t1_shared_cache_hits_across_ctx_instances():
    """T-1：共享缓存跨 bar 生效——第 2 个 ctx 不再进 loader。"""
    calls = {"n": 0}

    def loader(symbol: str):
        calls["n"] += 1
        return [_Bar(D1), _Bar(D2), _Bar(D3)]

    cache: BoundedDict = BoundedDict(maxsize=10, name="t")
    ctx1 = _ctx(loader, cache=cache, now=D2)
    ctx2 = _ctx(loader, cache=cache, now=D3)
    ctx1.history("000001.SZ", D2)
    ctx2.history("000001.SZ", D3)
    assert calls["n"] == 1, f"loader 被调用 {calls['n']} 次（期望 1）"
    assert cache.hits == 1 and cache.misses == 1


def test_t2_end_before_first_day():
    """T-2：`end` 早于首日 ⇒ 空列表，不抛异常。"""
    ctx = _ctx(lambda s: [_Bar(D1), _Bar(D2)])
    assert ctx.history("000001.SZ", TradingDate.from_iso(2015, 1, 2)) == []


def test_t3_end_after_last_day():
    """T-3：`end` 晚于末日 ⇒ 取最后 n_bars（不足则全部）。"""
    bars = [_Bar(D1), _Bar(D2), _Bar(D3)]
    ctx = _ctx(lambda s: bars)
    end = TradingDate.from_iso(2015, 1, 9)
    assert ctx.history("000001.SZ", end, n_bars=2) == bars[-2:]
    assert ctx.history("000001.SZ", end, n_bars=5) == bars


def test_t4_empty_series():
    """T-4：空序列 ⇒ 空列表，不抛异常。"""
    ctx = _ctx(lambda s: [])
    assert ctx.history("000001.SZ", D3) == []


def test_t5_n_bars_non_positive():
    """T-5：`n_bars=0` / 负数 ⇒ 空列表（与旧行为一致）。"""
    ctx = _ctx(lambda s: [_Bar(D1), _Bar(D2)])
    assert ctx.history("000001.SZ", D2, n_bars=0) == []
    assert ctx.history("000001.SZ", D2, n_bars=-3) == []
