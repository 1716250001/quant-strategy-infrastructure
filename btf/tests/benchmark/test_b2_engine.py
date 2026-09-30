# -*- coding: utf-8 -*-
"""L5 基准 B2：事件循环 10 年月调仓 <15s（PoC-2 任务 2.8 验收）。

B2 语义（18 号计划 2.8）：沪深300 轮动 10 年月调仓（MemoryFeed→真实 Feed）。
    [MemoryFeed 段] 合成小数据集冒烟：引擎全链路正确性（成交/快照非平凡）；
    [真实 Feed 段] 000300.SH 权重前 20 等权、每月首个交易日决策（PIT：用
    上月末成分快照）、次日开盘执行（NextOpen），2016-01-01..2025-12-31。

预算分解（B1 证据底座）：全迭代底座 ≈7.15s（65 年文件 I/O+排序+pylist），
引擎逐日消费余量 ≈7.85s（事件序+撮合 bisect+快照；禁全截面物化——
_LazyCrossSection 单行构造，B1 物化段 22s 禁区）。

窗口依据：index_weight 000300.SH 快照 20160129 起（128 月），
2016-2025 为完整十年 PIT 干净窗口（2015 无成分数据，不前向填充）。
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import TushareParquetFeed
from btf.data.memory import MemoryFeed
from btf.data.state import StateSynthesizer
from btf.domain.types import TradingDate
from btf.engine.loop import Engine
from btf.execution.cost import FlatRateCostModel
from btf.execution.handler import NextOpenHandler
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

from tests.fixtures.scenarios import make_bar, make_state

pytestmark = [pytest.mark.l5]

ROOT = Path(MARKET_DATA_DIR)
#: 软目标（09 §14.5 报告口径，**隔离环境**）：超此值仅告警/打印，不判失败。
B2_SOFT_TARGET_SECONDS = 15.0
#: 硬门槛（Z-4，19 号 §26.4 裁决 3）：墙钟在**负载机**（并发 CLI 回测/探针）
#: 上不可复现——隔离实测 14.03s / 原门槛 15.00s 仅 **6.5% 余量**，任何并发
#: 都会假失败。故硬门槛放宽至 20s（隔离基线的 **1.42x**，余量 43%），
#: 真正的算法回归守卫交给 **B3 十年 < 60s**（余量 1.82x）与 perf 套件。
B2_LIMIT_SECONDS = 20.0
START = TradingDate(date(2016, 1, 1))
END = TradingDate(date(2025, 12, 31))


def _load_hs300_monthly() -> dict[str, list[tuple[str, float]]]:
    """000300.SH 月度成分快照：{yyyymm: [(con_code, weight)…按权重降序]}。"""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    t = pq.read_table(ROOT / "index_weight" / "index_weight.parquet")
    m = t.filter(pc.equal(t.column("index_code"), "000300.SH"))
    out: dict[str, list[tuple[str, float]]] = {}
    for code, ymd, weight in zip(m.column("con_code").to_pylist(),
                                 m.column("trade_date").to_pylist(),
                                 m.column("weight").to_pylist(), strict=True):
        out.setdefault(ymd[:6], []).append((code, weight))
    for v in out.values():
        v.sort(key=lambda x: (-x[1], x[0]))     # 确定性：权重降序、code 决胜
    return out


class Hs300TopN(StrategyBase):
    """沪深300 权重前 N 等权，每月首个交易日决策（新月首日 on_close 提交，
    次日开盘执行；快照取**上月末**——PIT：快照日 < 决策日，v0.5 V5-4 订正
    「当月快照」前视）。"""

    def __init__(self, monthly: dict[str, list[tuple[str, float]]], top_n: int = 20):
        self._monthly = monthly
        self._top_n = top_n

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        if date.iso.day <= 3:                     # 月首首交易日
            month = date.to_ymd()[:6]
            y, m = int(month[:4]), int(month[4:6])
            prev = f"{y - 1}12" if m == 1 else f"{y}{m - 1:02d}"
            snap = self._monthly.get(prev)
            if snap:
                picks = [code for code, _ in snap[: self._top_n]]
                weight = 1.0 / len(picks)
                ctx.submit_target(TargetPortfolio(
                    date, {code: weight for code in picks}))


class TestB2MemorySmoke:
    """MemoryFeed 段：合成 3 标的 × 40 交易日，月调仓全链路冒烟。"""

    def test_monthly_rebalance_smoke(self, tmp_path: Path):
        days = [TradingDate(date(2020, 1, 1) + __import__("datetime").timedelta(days=i))
                for i in range(40)]
        days = [d for d in days if d.iso.weekday() < 5]        # 工作日
        bars, states = [], {}
        for i, d in enumerate(days):
            for j, sym in enumerate(("000001.SZ", "600519.SH", "300750.SZ")):
                px = 10.0 + i * 0.1 + j
                bars.append(make_bar(sym, d, px, px, px, px, px))
                states[(d.to_ymd(), sym)] = make_state(sym, d, pre=px)
        feed = MemoryFeed(bars, states, dates=days)

        class TwoAssets(StrategyBase):
            def on_close(self, ctx, date):
                if date.iso.day <= 3:
                    ctx.submit_target(TargetPortfolio(
                        date, {"000001.SZ": 0.5, "600519.SH": 0.5}))

        r = Engine(feed, TwoAssets(), start=days[0], end=days[-1],
                   initial_cash=1_000_000.0,
                   handler=NextOpenHandler(cost_model=FlatRateCostModel()),
                   event_log_path=tmp_path / "ev.jsonl").run()
        assert r.n_days == len(days)
        assert len(r.fills) >= 3                   # 建仓 + 再平衡确有成交
        assert r.snapshots[-1].total_value > 0
        assert (tmp_path / "ev.jsonl").exists()


@pytest.mark.skipif(not (ROOT / "daily").is_dir(), reason="主库不可用")
class TestB2RealFeed:
    """真实 Feed 段：沪深300 前 20 权重月调仓 10 年 <15s。"""

    def test_b2_hs300_monthly_10y(self, tmp_path: Path):
        monthly = _load_hs300_monthly()
        assert "201601" in monthly and len(monthly) >= 120

        # R4（19 号 §48）：状态面板**构造期注入**（feed 已不再 import state）
        feed = TushareParquetFeed(ROOT, state_provider=StateSynthesizer(ROOT))
        t0 = time.perf_counter()
        r = Engine(
            feed, Hs300TopN(monthly, top_n=20),
            start=START, end=END,
            initial_cash=1_000_000.0,
            handler=NextOpenHandler(cost_model=FlatRateCostModel()),
            event_log_path=tmp_path / "events.jsonl",
        ).run()
        elapsed = time.perf_counter() - t0

        n_events = sum(1 for _ in (tmp_path / "events.jsonl").open(encoding="utf-8"))
        band = ("软目标内" if elapsed <= B2_SOFT_TARGET_SECONDS
                else f"超软目标 {B2_SOFT_TARGET_SECONDS}s（负载？）")
        print(
            f"\nB2-real: {r.n_days} 交易日 | {len(r.orders)} 订单 | "
            f"{len(r.fills)} 成交 | {len(r.rejections)} 拒单 | "
            f"{n_events} 事件 | {elapsed:.2f}s"
            f"（{band}；硬门槛 {B2_LIMIT_SECONDS}s，Z-4）"
        )
        assert r.n_days > 2_400, "十年交易日数量级异常"
        assert len(r.fills) > 500, "月调仓成交量级异常（120 月×~20 标的）"
        assert elapsed < B2_LIMIT_SECONDS, (
            f"B2 超标：{elapsed:.2f}s ≥ 硬门槛 {B2_LIMIT_SECONDS}s（隔离基线"
            f"≈14s 的 1.4x）——逐日消费路径回查（全截面物化禁区/"
            f"states 触板裁剪/_LazyCrossSection bisect）"
        )
