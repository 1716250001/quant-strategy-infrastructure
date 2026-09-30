# -*- coding: utf-8 -*-
"""L1 单元测试：LazyStates 延迟物化语义（PoC-2 任务 2.8 第二刀验收）。

三断言：
    - 不访问不装载（loader 零调用——B2 豁免依据）；
    - 首次访问物化、值正确（Mapping 协议全接口）；
    - 重复访问只装载一次（缓存）。
"""
from __future__ import annotations

import pytest
from btf.domain.market import TradingState
from btf.strategy.context import LazyStates

from tests.fixtures.scenarios import D1, make_state

pytestmark = [pytest.mark.l1]


class _Counter:
    def __init__(self, table: dict[str, TradingState]):
        self.table = table
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.table


class TestLazyStates:
    def test_not_accessed_not_loaded(self):
        counter = _Counter({"000001.SZ": make_state("000001.SZ", D1, pre=10.0)})
        LazyStates(counter)
        assert counter.calls == 0

    def test_access_loads_once_and_values_correct(self):
        st = make_state("000001.SZ", D1, pre=10.0)
        counter = _Counter({"000001.SZ": st})
        lazy = LazyStates(counter)

        assert lazy.get("000001.SZ") is st          # 首次访问 → 物化
        assert counter.calls == 1
        assert lazy["000001.SZ"] is st
        assert "000001.SZ" in lazy
        assert set(lazy) == {"000001.SZ"}
        assert len(lazy) == 1
        assert lazy.get("600000.SH") is None        # 缺席 → None
        with pytest.raises(KeyError):
            _ = lazy["600000.SH"]
        assert counter.calls == 1                   # 全程只装载一次
