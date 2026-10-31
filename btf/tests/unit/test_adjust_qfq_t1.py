# -*- coding: utf-8 -*-
"""L1：第 3 批（19 号 §199）——`qfq` 同 bar 五字段只调一次因子。

运算顺序逐字保持 `(a × f) / base`（改成 `a * (f/base)` 会因浮点不结合翻转 9 笔
边界成交：`n_fills` 8,574→8,565，不报错——实测见派单 §一）。
"""
from __future__ import annotations

import pytest
from btf.data.adjust import TushareAdjustService
from btf.domain.market import Bar
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l1]

SYM = "600000.SH"


def _bar(ymd: str, px: float) -> Bar:
    d = TradingDate.from_ymd(ymd)
    return Bar(symbol=SYM, date=d, open=px, high=px + 0.5, low=px - 0.5,
               close=px + 0.2, volume=1000.0, amount=px * 1000.0,
               pre_close=px - 0.1)


def test_t1_qfq_single_factor_lookup_and_exact_order():
    """T-1：每 bar 只调一次 `_af`；五字段结果与手算 `(a × f) / base` 逐属性相等。"""
    calls = {"n": 0}
    svc = TushareAdjustService()
    svc._cache[SYM] = (["20160104", "20160105"], [1.0, 2.0])
    orig = svc.adj_factor

    def counting(symbol, date):
        calls["n"] += 1
        return orig(symbol, date)

    svc.adj_factor = counting                    # type: ignore[method-assign]
    bars = [_bar("20160104", 10.0), _bar("20160105", 11.0)]
    ref = TradingDate.from_ymd("20160104")
    out = svc.qfq(bars, ref)
    base = 1.0
    for src, got in zip(bars, out, strict=True):
        f = 1.0 if src.date.to_ymd() == "20160104" else 2.0
        assert got.open == src.open * f / base
        assert got.high == src.high * f / base
        assert got.low == src.low * f / base
        assert got.close == src.close * f / base
        assert got.pre_close == src.pre_close * f / base
    # 2 bar × 1 次 + base 1 次 = 3（旧实现为 2 × 5 + 1 = 11）
    assert calls["n"] == len(bars) + 1, f"adj_factor 调用 {calls['n']} 次"
