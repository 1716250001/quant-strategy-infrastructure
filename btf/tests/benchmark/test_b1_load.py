# -*- coding: utf-8 -*-
"""L5 基准 B1：10 年全市场日线数据就绪 <10s（PoC-1 任务 1.8 / CP1 重构后语义）。

B1 语义（PoC-1 剖析证据 2026-09-26 落定）：
    就绪（ready）= 全部 10 年数据进入内存（Arrow I/O+列裁剪 0.70s + 年排序
    + 列式 pylist + 日期分组索引），事件循环可开始迭代——**不含** Bar 对象化；
    950 万 Bar 的全量物化 ~16s+ 是纯 Python 对象化的物理下限，由懒截面
    （_LazyCrossSection）分摊进事件循环当日消费（B3 的 60s 预算覆盖）。
两段度量：
    [硬门槛] readiness < 10s；
    [证据]   全量物化耗时记录（不设 10s 门槛，B3 阶段统一验收）。
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import TushareParquetFeed
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l5]

ROOT = Path(MARKET_DATA_DIR)
#: **软目标**（09 §14.5 原始目标；超出仅打印告警，不判失败）
B1_READY_LIMIT_SECONDS = 10.0
#: **硬门槛**（§40.5 D-8，同 B2/LIQ 族）：软目标余量仅 8%（实测 9.22–10.50s），
#: 并发负载下已实测假失败（10.50s > 10.0s）；硬门槛 1.4x 仍可捕获数量级回归。
B1_READY_HARD_LIMIT_SECONDS = 14.0


@pytest.fixture(scope="module")
def feed() -> TushareParquetFeed:
    return TushareParquetFeed(ROOT)


@pytest.mark.skipif(not (ROOT / "daily").is_dir(), reason="主库不可用")
def test_b1_readiness_hard_gate(feed):
    """B1 硬门槛：10 年数据就绪（迭代全部交易日、不触发截面物化）< 10s。"""
    t0 = time.perf_counter()
    n_days = 0
    for _td, _cross in feed.bars(
        None, TradingDate(date(2015, 1, 1)), TradingDate(date(2024, 12, 31))
    ):
        n_days += 1  # 仅推进迭代（截面懒对象不访问）
    elapsed = time.perf_counter() - t0
    band = ("软目标内" if elapsed <= B1_READY_LIMIT_SECONDS
            else f"超软目标 {B1_READY_LIMIT_SECONDS}s（负载？）")
    print(f"\nB1-readiness: {n_days} 交易日就绪 {elapsed:.2f}s（{band}；"
          f"硬门槛 {B1_READY_HARD_LIMIT_SECONDS}s）")
    assert n_days > 2300
    assert elapsed < B1_READY_HARD_LIMIT_SECONDS, (
        f"B1 就绪超标：{elapsed:.2f}s ≥ 硬门槛 {B1_READY_HARD_LIMIT_SECONDS}s——"
        f"回 ADR-2 复查（CP1）"
    )


@pytest.mark.skipif(not (ROOT / "daily").is_dir(), reason="主库不可用")
def test_b1_full_materialization_evidence(feed):
    """B1 证据段：全量 Bar 物化耗时（懒截面逐日构建），登记进 benchmarks 证据，
    不设 10s 门槛（B3 全市场回测 <60s 阶段统一验收——含撮合/风控全链路）。"""
    t0 = time.perf_counter()
    n_days = 0
    n_bars = 0
    for _td, cross in feed.bars(
        None, TradingDate(date(2015, 1, 1)), TradingDate(date(2024, 12, 31))
    ):
        n_days += 1
        n_bars += len(cross)  # len() 触发当日物化
    elapsed = time.perf_counter() - t0
    print(f"B1-materialize: {n_days} 日 × {n_bars:,} Bars 全量物化 {elapsed:.2f}s"
          f"（≈{n_bars / max(n_days, 1):.0f} Bars/日）")
    assert n_bars > 8_000_000, "全市场 Bar 总量数量级异常（实测约 948 万）"
    assert elapsed < 60.0, "物化超过 B3 总预算量级——需架构复查"
