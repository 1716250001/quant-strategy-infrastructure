# -*- coding: utf-8 -*-
"""L1 单测：AdjustService 消费侧换算数学（PoC-3 任务 3.1 验收）。

公式唯一出处 04 §8.2.4（数据侧公式锚点，G1 黄金断言）：
    hfq(t) = price(t) × adj_factor(t)
    qfq(t) = price(t) × adj_factor(t) / adj_factor(ref)

缺失降级（11 §21.1 PoC-3 验证点）：
    当日无因子行 → 前向填充（最近先前因子）；
    起点前无任何因子 → 1.0 + 计数登记。

缓存注入测试（无 IO）：__init__ 不做 IO，覆盖 _cache 即纯数学。
"""
from __future__ import annotations

import pytest
from btf.data.adjust import TushareAdjustService
from btf.domain.types import TradingDate

from tests.fixtures.scenarios import make_bar

pytestmark = [pytest.mark.l1]

D1 = TradingDate.from_ymd("20200101")
D2 = TradingDate.from_ymd("20200102")
D3 = TradingDate.from_ymd("20200103")
D4 = TradingDate.from_ymd("20200104")     # 表内无此行（前向填充）
D0 = TradingDate.from_ymd("20191231")     # 早于表起点（回退 1.0）

SYM = "600900.SH"


def _svc() -> TushareAdjustService:
    s = TushareAdjustService()
    s._cache = {SYM: (["20200101", "20200102", "20200103"], [1.0, 2.0, 4.0])}
    return s


class TestAdjFactorLookup:
    def test_exact_hit(self):
        assert _svc().adj_factor(SYM, D2) == pytest.approx(2.0)

    def test_fill_forward(self):
        """非因子日（两次除权间）→ 最近先前因子；计数登记。"""
        s = _svc()
        assert s.adj_factor(SYM, D4) == pytest.approx(4.0)
        assert s.n_fill_forward == 1
        assert s.n_missing_fallback == 0

    def test_before_start_fallback(self):
        """早于表内首行 → 取最早因子（相对锚，比值自洽——因子覆盖
        晚于价格表的场景，如 600276 1997 上市/adj_factor 1999 起）。"""
        s = _svc()
        assert s.adj_factor(SYM, D0) == pytest.approx(1.0)   # 最早行恰为 1.0
        assert s.n_missing_fallback == 1

    def test_before_start_fallback_non_unit_anchor(self):
        """最早因子非 1 的表：起点前回退到最早因子（非 1.0 假设）。"""
        s = TushareAdjustService()
        s._cache = {SYM: (["20200102", "20200103"], [5.0, 8.0])}
        assert s.adj_factor(SYM, D0) == pytest.approx(5.0)

    def test_symbol_absent_from_table(self):
        """表内全无该标的 → 1.0（纯新股/缺数据；研究层不中断）。"""
        s = _svc()
        assert s.adj_factor("300999.SZ", D2) == pytest.approx(1.0)
        assert s.n_missing_fallback == 1


class TestHfq:
    def test_hfq_scales_all_prices(self):
        """close 10 @D2（af=2）→ hfq close 20；五价字段全换算。"""
        s = _svc()
        bar = make_bar(SYM, D2, o=10.0, h=11.0, lo=9.0, c=10.0, pre=10.0)
        (out,) = s.hfq([bar])
        assert out.close == pytest.approx(20.0)
        assert out.open == pytest.approx(20.0)
        assert out.high == pytest.approx(22.0)
        assert out.low == pytest.approx(18.0)
        assert out.pre_close == pytest.approx(20.0)

    def test_hfq_series_relative_growth(self):
        """hfq 序列跨除权日：价格 10→5（10送10）× af 1→2 → hfq 恒 10。"""
        s = _svc()
        bars = [
            make_bar(SYM, D1, o=10.0, h=10.0, lo=10.0, c=10.0, pre=10.0),
            make_bar(SYM, D2, o=5.0, h=5.0, lo=5.0, c=5.0, pre=5.0),
        ]
        out = s.hfq(bars)
        assert [b.close for b in out] == pytest.approx([10.0, 10.0])


class TestQfq:
    def test_qfq_interval_basis(self):
        """区间前复权（ref=回测起点 D1, af=1）：D2 close 10×2/1=20。"""
        s = _svc()
        bar = make_bar(SYM, D2, o=10.0, h=10.0, lo=10.0, c=10.0, pre=10.0)
        (out,) = s.qfq([bar], D1)
        assert out.close == pytest.approx(20.0)

    def test_qfq_full_history_basis(self):
        """全历史前复权（ref=最新 D3, af=4）：D2 10×2/4=5（基准漂移
        防护——ref 选择即口径选择）。"""
        s = _svc()
        bar = make_bar(SYM, D2, o=10.0, h=10.0, lo=10.0, c=10.0, pre=10.0)
        (out,) = s.qfq([bar], D3)
        assert out.close == pytest.approx(5.0)

    def test_qfq_empty_series(self):
        assert _svc().qfq([], D1) == []
