# -*- coding: utf-8 -*-
"""L1 单测：RC-15（2026-10-05）Walk-Forward 窗口切分。

纪律锚点（派单 §5）：**强制时间顺序**（只用交易日历、**禁用随机划分**——
本模块可被静态审查：源码零 `random` 字样）；支持 purge + embargo；
窗口/参数非法即 `ValueError`（fail-closed）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from btf.domain.types import TradingDate
from btf.optimize.walkforward import split_windows

pytestmark = [pytest.mark.l1]


def _days(n: int) -> list[TradingDate]:
    base = date(2015, 1, 5)
    return [TradingDate.from_ymd((base + timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(n)]


class TestSplitWindows:
    def test_rolling_basic(self):
        days = _days(60)
        ws = split_windows(days, train=20, test=10, step=10, mode="rolling")
        assert len(ws) == 4
        assert (ws[0].train_start, ws[0].train_end) == (days[0], days[19])
        assert (ws[0].test_start, ws[0].test_end) == (days[20], days[29])
        # 滚动：第 2 折训练窗起点前移 step
        assert ws[1].train_start == days[10]
        assert ws[1].test_start == days[30]
        # 最后一折测试窗贴住日历末端（不越界）
        assert ws[-1].test_end == days[59]

    def test_step_defaults_to_test(self):
        days = _days(60)
        a = split_windows(days, train=20, test=10)
        b = split_windows(days, train=20, test=10, step=10)
        assert [w.as_dict() for w in a] == [w.as_dict() for w in b]

    def test_anchored_extends_train(self):
        days = _days(60)
        ws = split_windows(days, train=20, test=10, step=10, mode="anchored")
        assert len(ws) == 4
        assert ws[1].train_start == days[0]          # 锚定首日
        assert ws[1].train_end == days[29]           # 训练窗加长
        assert ws[1].test_start == days[30]

    def test_purge_embargo_gap(self):
        days = _days(60)
        ws = split_windows(days, train=20, test=10, step=10, purge=2,
                           embargo=3)
        # gap = purge + embargo = 5：test_start = train_end 之后第 6 个交易日
        assert ws[0].test_start == days[25]
        assert ws[0].test_end == days[34]
        assert len(ws) == 3                          # 更长的 gap ⇒ 折数减少

    def test_insufficient_days_rejected(self):
        with pytest.raises(ValueError, match="窗口不足"):
            split_windows(_days(25), train=20, test=10)

    def test_invalid_params_rejected(self):
        days = _days(60)
        with pytest.raises(ValueError, match="mode"):
            split_windows(days, train=20, test=10, mode="random")
        with pytest.raises(ValueError, match="train/test"):
            split_windows(days, train=0, test=10)
        with pytest.raises(ValueError, match="step"):
            split_windows(days, train=20, test=10, step=0)
        with pytest.raises(ValueError, match="purge/embargo"):
            split_windows(days, train=20, test=10, purge=-1)

    def test_no_random_source_in_module(self):
        """静态审查：切分实现**零随机源**（"禁止随机划分"的机械守卫）。"""
        import inspect

        import btf.optimize.walkforward as wf

        src = inspect.getsource(wf)
        assert "random" not in src, "Walk-Forward 模块出现随机源（禁止随机划分）"

    def test_windows_are_time_ordered(self):
        days = _days(60)
        for mode in ("rolling", "anchored"):
            ws = split_windows(days, train=20, test=10, mode=mode)
            for w in ws:
                assert w.train_start < w.train_end < w.test_start < w.test_end
