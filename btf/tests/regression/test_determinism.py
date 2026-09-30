# -*- coding: utf-8 -*-
"""L3 回归测试：双跑确定性（PoC-2 任务 2.7 验收；08 §12.1 同种子+同配置双跑 diff=0）。

验收锚点：同配置跑两遍 events.jsonl 逐行哈希比对；
判据：事件流逐字节一致 + 快照序列逐值一致（含浮点 total_value/drawdown）。

覆盖路径：买入/卖出成交、涨停拒单、停牌拒单、T+0 ETF 当日往返、
空仓移除——全部事件类型走一遍（等价于事件编码器的双跑稳定证）。
真实 Feed 用例：parquet 读取 + 引擎全链路确定性（2015 全年，两标的月调仓）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from btf.domain.types import TradingDate
from btf.engine.loop import Engine
from btf.execution.cost import FlatRateCostModel
from btf.execution.handler import NextOpenHandler
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

from tests.fixtures.scenarios import D1, D5, combined_scenario

pytestmark = [pytest.mark.l3]


class MultiPathStrategy(StrategyBase):
    """多路径覆盖：D1 建仓股票+ETF；D2 清仓股票、ETF 当日往返不可行（T+0
    经由目标重提交）；D3 涨停标的建仓（D4 撮合拒/成交混合）。"""

    def __init__(self) -> None:
        self.n = 0

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        ymd = date.to_ymd()
        if ymd == "20150105":
            ctx.submit_target(TargetPortfolio(date, {
                "000001.SZ": 0.3, "511380.SH": 0.2, "600000.SH": 0.2,
            }))
        elif ymd == "20150106":
            ctx.submit_target(TargetPortfolio(date, {"511380.SH": 0.4}))
        elif ymd == "20150107":
            ctx.submit_target(TargetPortfolio(date, {
                "000002.SZ": 0.5, "600000.SH": 0.3,
            }))
        elif ymd == "20150108":
            ctx.submit_target(TargetPortfolio(date, {}))       # 全清


def _run_memory(tmp: Path) -> tuple[bytes, list]:
    sc = combined_scenario()
    path = tmp / f"events-{id(sc)}.jsonl"
    engine = Engine(
        sc.feed, MultiPathStrategy(), start=D1, end=D5,
        initial_cash=1_000_000.0,
        handler=NextOpenHandler(cost_model=FlatRateCostModel(),
                                instruments=sc.instruments),
        instruments=sc.instruments,
        event_log_path=path,
    )
    r = engine.run()
    digest = hashlib.sha256(path.read_bytes()).hexdigest().encode()
    return digest, r.snapshots


class TestDeterminismMemory:
    def test_double_run_identical(self, tmp_path: Path):
        """MemoryFeed 双跑：事件流哈希 + 快照序列逐值全等。"""
        h1, s1 = _run_memory(tmp_path)
        h2, s2 = _run_memory(tmp_path)
        assert h1 == h2
        assert len(s1) == len(s2) == 5
        for a, b in zip(s1, s2, strict=True):
            assert a == b          # frozen dataclass 全等（含浮点逐位）
        # 事件量非平凡（多路径确实发生）
        assert h1 != hashlib.sha256(b"").hexdigest().encode()


class TestDeterminismRealFeed:
    """真实 Feed（主库 parquet）全链路双跑：读取+撮合+记账确定性。"""

    def test_real_feed_double_run(self, tmp_path: Path):
        pytest.importorskip("pyarrow")
        from btf.data.feed import TushareParquetFeed
        from btf.data.state import StateSynthesizer

        class MonthlyTwoAssets(StrategyBase):
            """月度等权两标的（000001/600519）——2015 全年每月首日决策。"""

            def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
                if date.iso.day <= 3:
                    ctx.submit_target(TargetPortfolio(date, {
                        "000001.SZ": 0.5, "600519.SH": 0.5,
                    }))

        def run_once(tag: str) -> bytes:
            # R4（19 号 §48）：状态面板构造期注入（feed 不再 import state）
            feed = TushareParquetFeed(
                state_provider=StateSynthesizer())
            path = tmp_path / f"events-{tag}.jsonl"
            r = Engine(
                feed, MonthlyTwoAssets(),
                start=TradingDate.from_ymd("20150101"),
                end=TradingDate.from_ymd("20151231"),
                initial_cash=1_000_000.0,
                handler=NextOpenHandler(cost_model=FlatRateCostModel()),
                event_log_path=path,
            ).run()
            assert r.n_days > 230            # 2015 全年交易日
            assert len(r.fills) > 20         # 12 月度调仓确有成交
            return hashlib.sha256(path.read_bytes()).hexdigest().encode()

        assert run_once("a") == run_once("b")
