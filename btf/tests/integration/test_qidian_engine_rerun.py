# -*- coding: utf-8 -*-
"""奇点战法**引擎级复跑**集成（M3 6.3；V3-1 混合表 Feed + V3-2 ETF 限价）。

覆盖：
    1. ETF 限价通道：`etf_limit`（起点 2019-06-26）为池内 ETF 提供涨跌停价；
       股票仍走 `stk_limit`；**起点之前**由 BoardRule ±10% 依 pre_close 计算
       回退并登记披露（E3，不静默）；
    2. 引擎级复跑（有界窗口）：混合表 Feed 装配 → 预载 → 引擎执行 → 落盘
       指标齐备（`btf` 侧全链路：截面/历史/状态/撮合/快照/指标）；
    3. ETF 身份与 BoardRule：池内可转债 ETF（511180/511380）经推断为
       BOND 子类 → **T+0**。

主库不可用 → skip（不静默通过）。全区间（2019-06-26 起）净值复跑由
`tools/run_qidian_rerun.py` 承担（分钟级，不入 CI）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import MixedDailyFeed
from btf.data.state import StateSynthesizer
from btf.domain.types import EtfSubclass, TradingDate, rule_for
from btf.experiment.store import LocalRunStore
from btf.runtime import BTFRuntime
from btf.strategy.qidian import load_reference, pool_of

pytestmark = [pytest.mark.l4]

ROOT = Path(MARKET_DATA_DIR)
START, END = "2026-01-02", "2026-09-23"          # 有界窗口（CI 秒级）
LIMIT_START = "20190626"                          # etf_limit 起点（E3）


def _feed() -> MixedDailyFeed:
    """构造混合表 Feed（**注入状态面板**）。

    R4（19 号 §48）：`feed` 不再 import `state`，状态合成器改为**构造期注入**；
    经 `BTFRuntime` 运行时由装配根自动注入，直接构造的测试显式传入。
    """
    return MixedDailyFeed(state_provider=StateSynthesizer())


def _skip_if_no_data() -> None:
    if not (ROOT / "fund_daily").is_dir() or not (ROOT / "etf_limit").is_dir():
        pytest.skip("主库 fund_daily/etf_limit 不可用")


@pytest.fixture(scope="module")
def pool() -> dict:
    return pool_of(load_reference())


class TestEtfLimitChannel:
    """V3-2：ETF 涨跌停价通道。"""

    def test_etf_limit_from_table(self):
        _skip_if_no_data()
        feed = _feed()
        state = feed.trading_states(
            TradingDate.from_ymd("20260923"), ["511380.SH"])["511380.SH"]
        assert state.limit_up_price and state.limit_down_price
        assert state.limit_up_price > state.limit_down_price

    def test_stock_still_from_stk_limit(self):
        _skip_if_no_data()
        feed = _feed()
        state = feed.trading_states(
            TradingDate.from_ymd("20260923"), ["000001.SZ"])["000001.SZ"]
        assert state.limit_up_price and state.limit_down_price

    def test_fallback_before_etf_limit_start(self, pool):
        """E3 回退：`etf_limit` 起点之前 → BoardRule ±10% 计算 + 登记披露。

        须取「起点之前已有 fund_daily 行情」的池内 ETF（多数池内 ETF 上市晚于
        2019-06-26，无行情则回退无从计算——那属正确行为，不是回退失效）。
        """
        _skip_if_no_data()
        feed = _feed()
        day = TradingDate.from_ymd("20190625")     # 起点前一日
        candidates = [code for code in sorted(pool)
                      if feed.bars_of(code, day, day)]
        if not candidates:
            pytest.skip("池内无 2019-06-25 前上市的 ETF（无行情则回退无输入）")
        code = candidates[0]
        state = feed.trading_states(day, [code])[code]
        assert state.limit_up_price is not None and state.limit_down_price is not None
        assert state.limit_up_price > state.limit_down_price
        assert day.to_ymd() in feed.states.fallback_limit_dates
        notes = feed.states.degraded_notes()
        assert notes and "etf_limit 起点 2019-06-26" in notes[0]

    def test_no_fallback_at_or_after_start(self):
        """起点当日及之后走 `etf_limit` 表值 → 无回退登记。"""
        _skip_if_no_data()
        feed = _feed()
        feed.trading_states(TradingDate.from_ymd(LIMIT_START), ["511380.SH"])
        assert not feed.states.fallback_limit_dates


@pytest.fixture(scope="module")
def rerun_result(pool, tmp_path_factory):
    """有界窗口引擎级复跑（模块级：一次装配一次执行）。"""
    _skip_if_no_data()
    instruments = sorted(pool)
    indices = sorted({entry["code"] for info in pool.values()
                      for entry in info["indices"]})
    config = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "btf.strategy.qidian:QidianStrategy",
            "params": {"weight": 0.02},
            "universe": {"source": "explicit", "symbols": instruments},
            "period": {"start": START, "end": END},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": "mixed"},
        # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
        "risk": {"rules": [], "allow_empty_chain": True},
        "seed": {"master": 20260927},
    }
    runtime = BTFRuntime().load_config(config).build()
    start, end = (TradingDate.from_ymd(START.replace("-", "")),
                  TradingDate.from_ymd(END.replace("-", "")))
    runtime.feed.preload(instruments + indices, start, end)
    store = LocalRunStore(tmp_path_factory.mktemp("qidian_rerun"))
    run = runtime.run(store=store)
    return runtime, store, run


class TestEngineRerun:
    """引擎级复跑（有界窗口；全区间见 tools/run_qidian_rerun.py）。"""

    def test_mixed_feed_assembled_with_cross_section(self, rerun_result):
        runtime, _store, run = rerun_result
        assert getattr(runtime.feed, "requires_explicit_symbols", False) is True
        assert run.n_days > 150                       # 2026-01-02 ~ 09-23 ≈ 180 日
        assert len(runtime.instruments) == 43

    def test_run_produces_fills_and_metrics(self, rerun_result):
        _runtime, _store, run = rerun_result
        assert run.fills, "有界窗口内应有成交（池内双确认买入）"
        for key in ("final_nav", "total_return", "max_drawdown", "n_fills",
                    "total_fees", "sharpe_ratio", "n_trading_days"):
            assert key in run.metrics, key
        assert run.metrics["n_fills"] == float(len(run.fills))
        assert run.metrics["final_nav"] > 0

    def test_artifacts_persisted_and_verifiable(self, rerun_result):
        runtime, store, run = rerun_result
        bundle = store.load(run.run_id)
        assert bundle.manifest.status == "COMPLETED"
        assert bundle.snapshots and bundle.fills
        report = runtime.verify_run(run.run_id, store=store)
        assert report["match"], "R1 复核：重算 + 落盘 digest 双项一致"

    def test_convertible_bond_etf_is_t0(self, rerun_result):
        runtime, _store, _run = rerun_result
        for code in ("511180.SH", "511380.SH"):
            instrument = runtime.instruments[code]
            assert instrument.etf_subclass is EtfSubclass.BOND
            assert rule_for(instrument).t_plus == 0


class TestDegradationDisclosure:
    def test_strategy_reports_unusable_index_pairs(self, pool):
        """配对指数不可用（如 H30315.CSI 仅 close）→ 策略显式披露。"""
        _skip_if_no_data()
        unusable = [code for code in pool
                    if all(entry["code"] in {"H30315.CSI"}
                           for entry in pool[code]["indices"])]
        if not unusable:
            pytest.skip("真源池未含已知无 OHLC 指数")
        instruments = sorted(pool)
        indices = sorted({entry["code"] for info in pool.values()
                          for entry in info["indices"]})
        config = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "btf.strategy.qidian:QidianStrategy",
                "params": {"weight": 0.02},
                "universe": {"source": "explicit", "symbols": instruments},
                "period": {"start": START, "end": END},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": "mixed"},
            # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
            "risk": {"rules": [], "allow_empty_chain": True},
            "seed": {"master": 20260927},
        }
        runtime = BTFRuntime().load_config(config).build()
        start, end = TradingDate.from_ymd("20260302"), TradingDate.from_ymd("20260923")
        runtime.feed.preload(instruments + indices, start, end)
        runtime.run(persist=False)
        notes = runtime.strategy.degraded_notes()
        assert notes, "配对指数不可用须披露"
        assert any(any(code in note for code in unusable) for note in notes)
