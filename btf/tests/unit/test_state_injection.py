# -*- coding: utf-8 -*-
"""状态面板**构造期注入**（R4；19 号 §47.4 余项 ① → §48 执行记录）。

为什么需要本组用例：`feed → state` 曾是契约新 8（data 域互不依赖）的**唯一豁免边**
（`feed.py` 懒 import `StateSynthesizer`）。反转后：

    feed 不再 import state；状态合成器由**装配根注入**
    （`BTFRuntime.build` → `attach_state_provider`）；
    直接构造 Feed 的场景须显式 `state_provider=`，**未注入而取状态即报错**。

断言覆盖（铁律新 15：防线须可被证伪）：
    ① 未注入 → `states` / `trading_states` **必须**抛 RuntimeError（不静默降级）；
    ② 注入后可用 + 幂等覆盖；
    ③ `MixedDailyFeed` 透传到股票侧原型（状态**同一对象**，不各建一份）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import MixedDailyFeed, TushareParquetFeed
from btf.data.state import StateSynthesizer
from btf.domain.types import TradingDate

ROOT = Path(MARKET_DATA_DIR)


class TestUninjectedRaises:
    """① 未注入 → 显式报错（状态面板是撮合/风控唯一依据，缺它必须失败）。"""

    def test_states_property_raises(self):
        feed = TushareParquetFeed(ROOT)               # 不传 state_provider
        with pytest.raises(RuntimeError, match="状态面板未注入"):
            _ = feed.states

    def test_trading_states_raises(self):
        feed = TushareParquetFeed(ROOT)
        with pytest.raises(RuntimeError, match="状态面板未注入"):
            feed.trading_states(TradingDate.from_ymd("20240102"), ["000001.SZ"])

    def test_error_message_is_actionable(self):
        """报错须给出可执行的修复指引（铁律新 16：不空、不误导）。"""
        feed = TushareParquetFeed(ROOT)
        with pytest.raises(RuntimeError) as ei:
            _ = feed.states
        msg = str(ei.value)
        assert "state_provider" in msg and "StateSynthesizer" in msg, msg


class TestInjection:
    """② 注入可用 + 幂等覆盖；③ 混合表透传同一对象。"""

    def test_constructor_injection(self):
        feed = TushareParquetFeed(ROOT) if not ROOT.is_dir() else (
            TushareParquetFeed(ROOT, state_provider=object()))
        assert feed.states is not None

    def test_attach_overrides(self):
        feed = TushareParquetFeed(ROOT)
        first, second = object(), object()
        feed.attach_state_provider(first)
        assert feed.states is first
        feed.attach_state_provider(second)            # 幂等：后注入覆盖前者
        assert feed.states is second

    def test_mixed_feed_passes_through_same_object(self):
        provider = object()
        feed = MixedDailyFeed(ROOT, state_provider=provider)
        assert feed.states is provider                # 透传而非各建一份
        assert feed._delegate.states is provider
        other = object()
        feed.attach_state_provider(other)             # 注入同步到委托层
        assert feed.states is other and feed._delegate.states is other

    @pytest.mark.skipif(not (ROOT / "daily").is_dir(), reason="主库不可用")
    def test_real_synthesizer_usable_after_injection(self):
        """真合成器注入后 `trading_states` 可返回状态（端到端小样）。"""
        feed = TushareParquetFeed(ROOT, state_provider=StateSynthesizer(ROOT))
        states = feed.trading_states(
            TradingDate.from_ymd("20240102"), ["000001.SZ"])
        assert "000001.SZ" in states
