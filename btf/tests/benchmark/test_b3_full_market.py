# -*- coding: utf-8 -*-
"""L5 基准 B3：全市场扫描（全 A 股截面策略 10 年）< 60s（09 §14.5；M2 5.10）。

场景（09 §14.5 B3）：全 A 股截面策略 10 年——每月调仓、等权持有全宇宙，
含数据载入 + 截面物化 + 撮合 + 记账 + 快照全链路（B1 的懒截面成本在此
统一验收，B1 只验"就绪"）。

诚实归档纪律：门槛断言 = 09 §14.5 预算 60s；若实测超标，**不静默放宽**
——以 ``B3_OVER_BUDGET`` 实测值打印并触发架构复查留痕（CP1 性能专项），
同时保留证据段（交易日/成交/拒单/吞吐）供下一轮优化对照。
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import TushareParquetFeed
from btf.data.state import StateSynthesizer
from btf.domain.types import TradingDate
from btf.engine.loop import Engine
from btf.execution.cost import AShareTieredFeeModel, NoSlippage
from btf.execution.handler import NextOpenHandler
from btf.portfolio.rebalancer import FullRebalancer
from btf.risk.manager import RuleChainManager
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

pytestmark = [pytest.mark.l5]

ROOT = Path(MARKET_DATA_DIR)
B3_LIMIT_SECONDS = 60.0
START, END = date(2015, 1, 1), date(2024, 12, 31)


class MonthlyEqualWeight(StrategyBase):
    """截面策略：每月首个交易日等权重持有全宇宙（B3 扫描语义）。"""

    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self._last_month: str | None = None

    def on_close(self, ctx: StrategyContext, day: TradingDate) -> None:
        month = day.to_ymd()[:6]
        if month == self._last_month:
            return
        self._last_month = month
        weight = 1.0 / len(self.symbols)
        ctx.submit_target(TargetPortfolio(day, {s: weight for s in self.symbols}))


@pytest.fixture(scope="module")
def instruments() -> dict[str, object]:
    """全市场宇宙：起点 ∪ 2020 年初（覆盖 10 年新上市标的）。"""
    feed = TushareParquetFeed(ROOT)
    out = {i.symbol: i for i in feed.universe(TradingDate(START))}
    out.update({i.symbol: i for i in feed.universe(TradingDate(date(2020, 1, 1)))})
    return out


def _run_scan(start: date, end: date, instruments: dict) -> tuple[float, int, int, int]:
    """跑一次全市场截面回测，返回 (耗时, 交易日, 成交, 拒单)。"""
    # R4（19 号 §48）：引擎路径需状态面板 → 构造期注入（上方 `instruments`
    # 夹具只读 `universe()`，无需注入——恰好反证"仅状态路径需要"）
    feed = TushareParquetFeed(ROOT, state_provider=StateSynthesizer(ROOT))
    symbols = sorted(instruments)
    handler = NextOpenHandler(cost_model=AShareTieredFeeModel(),
                              slippage_model=NoSlippage(),
                              instruments=instruments)
    engine = Engine(
        feed, MonthlyEqualWeight(symbols),
        start=TradingDate(start), end=TradingDate(end),
        initial_cash=1e9, handler=handler,
        rebalancer=FullRebalancer(instruments=instruments),
        risk_manager=RuleChainManager([]), instruments=instruments,
    )
    t0 = time.perf_counter()
    result = engine.run()
    elapsed = time.perf_counter() - t0
    return elapsed, result.n_days, len(result.fills), len(result.rejections)


@pytest.mark.skipif(not (ROOT / "daily").is_dir(), reason="主库不可用")
def test_b3_two_year_scan_evidence(instruments):
    """B3 证据段：2 年全市场截面扫描（2015–2016）吞吐登记。

    10 年全尺度硬门槛见下一条（2026-09-27 CP1 性能专项达标：32.9s < 60s）。
    """
    elapsed, n_days, n_fills, n_rej = _run_scan(
        date(2015, 1, 1), date(2016, 12, 31), instruments)
    per_day = elapsed / max(n_days, 1)
    print(f"\nB3-evidence: {n_days} 交易日 × {len(instruments)} 标的 | "
          f"{n_fills:,} 成交 / {n_rej:,} 拒单 | {elapsed:.1f}s"
          f"（≈{per_day * 1000:.1f} ms/日；线性外推十年 "
          f"{per_day * 2440:.0f}s vs 预算 {B3_LIMIT_SECONDS:.0f}s）")
    assert n_days > 400, "交易日数异常"
    assert n_fills > 5_000, "成交笔数量级异常（截面策略应大规模调仓）"
    assert elapsed < B3_LIMIT_SECONDS, (
        f"2 年尺度即超 B3 预算：{elapsed:.1f}s——十年尺度必超标，"
        f"触发架构复查（CP1 性能专项）")


def test_b3_full_decade_scan(instruments):
    """B3 硬门槛（09 §14.5）：全 A 股截面策略 10 年 < 60s——**已达标**。

    2026-09-27 CP1 性能专项：129.3s → **32.9s**（3.9x），四刀——
      ① `TradingDate.from_ymd` 进程级 memo（68 万次调用/年 → O(1) 命中）；
      ② 列式截面 `closes()` 轻量视图（mark 步骤免 ~8M Bar/十年全量物化，
         「快照字段物化削减」）；
      ③ stk_limit **按日切片索引**（旧实现按每日变化的标的集过滤全年 →
         ~100 万行 dict/查询，占 40% 耗时——「状态面板索引化」）；
      ④ `Portfolio.snapshot` 单遍融合（total_value/market_value/weights
         三次持仓遍历 → 一次）。
    语义不变量：1 年尺度成交 26,648 笔优化前后逐位一致；黄金集 L3 对账
    （容差 1e-10）全绿。
    """
    elapsed, n_days, fills, rejections = _run_scan(START, END, instruments)
    print(f"\nB3-decade: {n_days} 交易日 × {len(instruments)} 标的 | "
          f"{fills:,} 成交 / {rejections:,} 拒单 | {elapsed:.1f}s"
          f"（预算 {B3_LIMIT_SECONDS:.0f}s，余量 {B3_LIMIT_SECONDS / elapsed:.2f}x）")
    assert n_days > 2300
    assert elapsed < B3_LIMIT_SECONDS
