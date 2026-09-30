# -*- coding: utf-8 -*-
"""L5 基准 B4：参数扫描 1000 组 × B2 场景（8 核进程池）< 30min（09 §14.5；v0.5 V5-3）。

场景：B2 同构（20 标的等权月调仓 10 年，flat_rate 费率）× grid_search
进程池。诚实归档纪律：以 N 组合实测墙钟**线性外推** 1000 组（不静默放宽）；
一致性锚点：同一组合在「进程池 vs 进程内单跑」的指标逐位一致。

性能依据（2026-09-27 实测，12 逻辑核）：标的谓词下推
（`iter_day_slices(symbols=…)`）令子集宇宙场景免扫全市场年表——
12 组合 × 8 进程 **8.0s** → 外推 1000 组 ≈ 664s（11.1min，余量 2.7x）。

⚠ **BB-1 回归留痕（2026-09-28，19 号 §40.5 D-7）**：数据指纹（BB-1）首版接在
`runtime.build()` → 网格**每组合各付一次**（10 年 6 表 fast 档实测 **7.2s**）→
本基准 12 组合 **8.0s → 43.8s、外推 664s → 3647s**（超 1800s 预算）。
已修：指纹只在**落盘路径**（`run(persist=True)`）计算 + 缺省 `meta` 档（毫秒级）
+ 进程内 memo。修复后实测 **11.1–11.6s**（外推 926–965s，余量 **~1.9x**）；
单组合 inline **2.92s**（2430 日 / 509 成交）＝纯装载+回测，指纹成本为零。
回归守卫：`tests/integration/test_data_fingerprint.py::test_grid_path_pays_nothing`
（`build()` + `persist=False` 后指纹耗时必须仍为 0）。

⚠ **EX-4 归因留痕（2026-09-29，19 号 §49.5 / OBS-5）**：EX-4 把本基准的 worker
数据路径改为经 `core` 委托（`feed._store.iter_day_slices` 等）。**A/B 实测证明
委托层零可测开销**——20 标的 × 十年 daily 全量流式：`core` **0.458 / 0.467s**
vs `parquet_reader` **0.463s**（交替两轮，首轮为页缓存冷启动）。本基准另一组实测：
**隔离单跑 20.0s**（外推 1670s，余量 **1.08x**）通过；**与其它 pytest 并发时
22.5s**（外推 1871s，余量 0.96x）**假失败** ⇒ **L5 基准须隔离单跑**（同 D-8 族：
墙钟门槛 + 本机漂移 + 并发负载）。预算 1800s 是 09 §14.5 的**需求**，不因机器态放宽。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.optimize.grid import GridSearchOptimizer

pytestmark = [pytest.mark.l5]

ROOT = Path(MARKET_DATA_DIR)
B4_LIMIT_SECONDS = 1800.0        # 1000 组 × B2 场景预算（09 §14.5）
N_COMBOS = 12                    # 证据样本（外推基数）
WORKERS = 8

SYMS = ["600519.SH", "601318.SH", "600036.SH", "000858.SZ", "000333.SZ",
        "600030.SH", "601988.SH", "600887.SH", "000001.SZ", "002415.SZ",
        "600276.SH", "601166.SH", "000651.SZ", "600900.SH", "601398.SH",
        "000002.SZ", "600028.SH", "601288.SH", "600009.SH", "000063.SZ"]


def _config() -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "btf.strategy.monthly:MonthlyEqualWeight",
            "params": {"symbols": SYMS},
            "universe": {"source": "explicit", "symbols": SYMS},
            "period": {"start": "2016-01-01", "end": "2025-12-31"},
            "initial_cash": 10_000_000,
        },
        "data": {"feed": "tushare_parquet",
                 "feed_params": {"root": str(ROOT)}},
        "execution": {"handler": "next_open", "cost_model": "flat_rate",
                      "rebalancer": "full"},
        # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
        "risk": {"rules": [], "allow_empty_chain": True},
    }


GRID = {"run.params.max_names": [2, 4, 6, 8, 10, 12],
        "run.params.rebalance_day": [1, 2]}


@pytest.mark.skipif(not (ROOT / "daily").is_dir(), reason="主库不可用")
def test_b4_grid_search_evidence():
    """B4 证据段：12 组合 × 8 进程实测 → 外推 1000 组 < 30min。"""
    report = GridSearchOptimizer(workers=WORKERS).run(
        _config(), GRID, objective="final_nav")
    assert report["n_combos"] == N_COMBOS
    assert all(r["n_days"] > 2400 for r in report["results"])


@pytest.mark.skipif(not (ROOT / "daily").is_dir(), reason="主库不可用")
def test_b4_grid_search_budget():
    """B4 硬门槛（09 §14.5）：1000 组 × B2 场景（8 进程）< 30min——外推实测。"""
    t0 = time.perf_counter()
    GridSearchOptimizer(workers=WORKERS).run(_config(), GRID,
                                             objective="final_nav")
    wall = time.perf_counter() - t0
    extrapolated = wall * 1000 / N_COMBOS
    print(f"\nB4: {N_COMBOS} 组合 × {WORKERS} 进程 {wall:.1f}s | "
          f"外推 1000 组 {extrapolated:.0f}s"
          f"（预算 {B4_LIMIT_SECONDS:.0f}s，余量 {B4_LIMIT_SECONDS / extrapolated:.2f}x）")
    assert extrapolated < B4_LIMIT_SECONDS, (
        f"B4 超标：外推 {extrapolated:.0f}s > {B4_LIMIT_SECONDS:.0f}s——"
        f"回查 worker 数据路径（谓词下推 / 年表缓存 / 进程数适配）")


@pytest.mark.skipif(not (ROOT / "daily").is_dir(), reason="主库不可用")
def test_b4_pool_matches_inline_single_run():
    """一致性锚点：进程池组合结果 == 进程内单跑（同链路确定性，跨进程）。"""
    grid = {"run.params.max_names": [8], "run.params.rebalance_day": [1]}
    pooled = GridSearchOptimizer(workers=2).run(
        _config(), grid, objective="final_nav")["results"][0]
    inline = GridSearchOptimizer(workers=0).run(
        _config(), grid, objective="final_nav")["results"][0]
    assert pooled["n_days"] == inline["n_days"]
    assert pooled["n_fills"] == inline["n_fills"]
    for key in ("final_nav", "total_return", "max_drawdown", "n_fills"):
        assert pooled["metrics"][key] == inline["metrics"][key], key
