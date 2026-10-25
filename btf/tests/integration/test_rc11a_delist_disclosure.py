# -*- coding: utf-8 -*-
"""L2 集成：RC-11-A（2026-10-05）退市披露——**只加披露，零断言破坏**。

两个半边（派单 §5 RC-11）：

1. **宇宙侧**（`universe_resolver._explicit`）：不在 `stock_basic` 在册池的
   代码**分流**——基金段（`is_fund_code`）走既有 ETF 身份推断（INFO，正常
   路径）；**股票段**判定 退市 / 未上市 / 未登记 → `warning` + 装配披露
   （`assembly_notes`）。**不阻断**、`Instrument` 不变。
2. **持仓侧**（`engine/loop.py` + `run_orchestrator`）：区间末仍持仓且已退市
   → 装配披露「标的 / 末次交易日 / 残值口径（按末次收盘价携带、未清算）」；
   `portfolio.py` **数值零改动**。

反向探针（防"恒现"）：无退市持仓的常规回测 ⇒ 该披露**不得出现**。

**CL-3（2026-10-06；19 号 §72.4）**：清算口径显式化——`carry`（默认＝现状，
零回归）/ `writeoff`（按 0 核销压力档：仅末日快照清零 + NAV 下调残值，核销
基准不取区间外价）。用例见文末 `TestDelistedSettlementCl3`。
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from btf import registry
from btf.data.memory import MemoryFeed
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.experiment.store import LocalRunStore
from btf.runtime import BTFRuntime
from btf.runtime.universe_resolver import UniverseResolver

from tests.fixtures.scenarios import (
    ALL_DATES,
    D1,
    D2,
    D3,
    Scenario,
    make_bar,
    make_state,
    t_plus1_scenario,
)

pytestmark = [pytest.mark.l2]

FEED_NAME = "rc11a_delist_feed"
SYM = "000033.SZ"          # 退市标的（D3 起无 bar + is_delisted）


# ─────────────────────────────────────────────────────────────
# 一、宇宙侧：缺失代码分流（退市 / 未上市 / 未登记 vs 基金段）
# ─────────────────────────────────────────────────────────────
class _FakeFeed:
    """带 `universe_span` 的测试 Feed（模拟 stock_basic 全历史并集）。"""

    def __init__(self, *, pool=(), ever=(), future=()) -> None:
        self._pool = set(pool)
        self._ever = set(ever)
        self._future = set(future)
        self._as_of = ""

    def universe(self, date: TradingDate) -> list[Instrument]:
        self._as_of = date.to_ymd()
        return [Instrument(s, AssetClass.STOCK) for s in sorted(self._pool)]

    def universe_span(self, start: TradingDate,
                      end: TradingDate) -> list[Instrument]:
        # 历史并集请求 end==as_of；未来并集请求 end=20991231
        pick = self._ever if end.to_ymd() <= self._as_of else self._future
        return [Instrument(s, AssetClass.STOCK) for s in sorted(pick)]


class TestUniverseMissingClassification:
    def test_stock_codes_classified_and_noted(self, caplog):
        feed = _FakeFeed(pool=["000001.SZ"], ever=["000033.SZ"],
                         future=["688999.SH"])
        notes: list[str] = []
        with caplog.at_level(logging.WARNING,
                             logger="btf.runtime.universe_resolver"):
            insts, _monthly = UniverseResolver(feed, notes=notes).resolve(
                {"source": "explicit",
                 "symbols": ["000001.SZ", "000033.SZ", "688999.SH",
                             "000404.SZ", "511380.SH"]},
                D1)
        # 不阻断：五个代码全部返回
        assert set(insts) == {"000001.SZ", "000033.SZ", "688999.SH",
                              "000404.SZ", "511380.SH"}
        joined = " | ".join(notes)
        assert "000033.SZ" in joined and "退市" in joined
        assert "688999.SH" in joined and "未上市" in joined
        assert "000404.SZ" in joined and "未登记" in joined
        # 基金段（511380.SH）⇒ 身份推断属正常路径：不进装配披露
        assert "511380.SH" not in joined
        # warn 级别（原实现 INFO 且措辞误导）：股票段缺失代码必有 WARNING
        warned = " ".join(r.getMessage() for r in caplog.records)
        assert "000033.SZ" in warned and "退市" in warned

    def test_feed_without_universe_span_says_undetermined(self):
        feed = SimpleNamespace(universe=lambda date: [])
        notes: list[str] = []
        UniverseResolver(feed, notes=notes).resolve(
            {"source": "explicit", "symbols": ["000033.SZ"]}, D1)
        assert "未判定" in " | ".join(notes)


# ─────────────────────────────────────────────────────────────
# 二、持仓侧：退市持仓残值披露（端到端；含反向探针）
# ─────────────────────────────────────────────────────────────
def _delist_scenario() -> Scenario:
    """D1/D2 有行情、D3 起退市（无 bar + is_delisted=True）；D5 仍持仓。"""
    bars = [
        make_bar(SYM, ALL_DATES[0], 10.0, 10.0, 10.0, 10.0, 10.0),
        make_bar(SYM, ALL_DATES[1], 10.2, 10.2, 10.2, 10.2, 10.0),
    ]
    states = {}
    for i, d in enumerate(ALL_DATES):
        states[(d.to_ymd(), SYM)] = make_state(
            SYM, d, pre=(10.0 if i == 0 else 10.2),
            delisted=(d >= D3))
    instruments = {SYM: Instrument(SYM, AssetClass.STOCK, board="main",
                                   lot_size=100)}
    return Scenario(
        MemoryFeed(bars, states, instruments, dates=ALL_DATES), instruments)


@pytest.fixture()
def delist_feed():
    feed = _delist_scenario().feed

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, _factory)
    return feed


def _cfg(symbol: str, feed: str) -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": symbol, "weight": 0.9},
            "universe": {"source": "explicit", "symbols": [symbol]},
            "period": {"start": "2015-01-05", "end": "2015-01-09"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": feed},
        "risk": {"rules": [], "allow_empty_chain": True},
    }


class TestDelistedHoldingDisclosure:
    def test_note_and_manifest_disclose_delist(self, delist_feed, tmp_path):
        """退市持仓 ⇒ 披露含三要素（标的 / 末次交易日 / 残值口径）并落 manifest。"""
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(_cfg(SYM, FEED_NAME)).build()
        run_id = rt.run(store=store).run_id

        joined = " | ".join(rt.assembly_notes)
        assert "退市持仓残值披露" in joined
        assert SYM in joined
        assert D2.to_ymd() in joined              # 末次交易日 = D2
        assert "未清算" in joined                  # 残值口径
        assert "零改动" in joined

        manifest = json.loads(
            (store.run_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
        assert any("退市持仓残值披露" in n
                   for n in manifest["assembly_notes"]), "披露未随产物落盘"

        # 报告渲染级：披露进入「口径与装配披露」区
        from btf.app.services import assemble_assumptions
        from btf.viz.report import ReportBuilder

        bundle = store.load(run_id)
        html = ReportBuilder(bundle, assemble_assumptions(store, bundle)).build()
        assert "退市持仓残值披露" in html

    def test_frozen_nav_unchanged_by_disclosure(self, delist_feed, tmp_path):
        """数值零改动：退市后市值 = 持仓量 × **末次收盘价**（残值携带语义不变）。"""
        rt = BTFRuntime().load_config(_cfg(SYM, FEED_NAME)).build()
        result = rt.run(persist=False)
        last = result.snapshots[-1]
        qty = last.positions_qty.get(SYM)
        assert qty and qty > 0
        assert last.market_value == pytest.approx(qty * 10.2, rel=1e-12)

    def test_no_delist_no_disclosure(self, tmp_path):
        """反向探针：无退市持仓的常规回测 ⇒ 该披露**不得出现**（防恒现）。"""
        feed = t_plus1_scenario().feed

        def _factory(**_params):
            return feed

        _factory.contract_version = CONTRACT_VERSION
        name = "rc11a_normal_feed"
        if name not in registry.available(registry.DATA_FEED):
            registry.register(registry.DATA_FEED, name, _factory)
        rt = BTFRuntime().load_config(_cfg("000001.SZ", name)).build()
        rt.run(persist=False)
        assert not any("退市持仓残值披露" in n for n in rt.assembly_notes)


# ─────────────────────────────────────────────────────────────
# 三、CL-3（19 号 §72.4）：退市清算口径开关
#   carry（默认）＝ 现状零回归；writeoff ＝ 按 0 核销（压力档）
# ─────────────────────────────────────────────────────────────
class TestDelistedSettlementCl3:
    """判据 A（carry 零回归）/ B（writeoff 恰好低残值）/ C（口径显式 + 防前视
    基准）/ D（披露随口径）——审查方独立复跑锚点见 18 号 §九·V。"""

    def _cfg_with_mode(self, mode: str | None) -> dict:
        cfg = _cfg(SYM, FEED_NAME)
        if mode:
            cfg["run"]["delisted_settlement"] = mode
        return cfg

    def test_carry_zero_regression(self):
        """判据 A：默认档（未声明口径）与显式 carry 数值逐位一致；
        残值携带语义不变、无核销。"""
        rt_default = BTFRuntime().load_config(self._cfg_with_mode(None)).build()
        r1 = rt_default.run(persist=False)
        rt_carry = BTFRuntime().load_config(
            self._cfg_with_mode("carry")).build()
        r2 = rt_carry.run(persist=False)

        assert r1.delisted_settlement == "carry"
        assert r1.delisted_writeoff_amount == 0.0
        assert r1.snapshots[-1] == r2.snapshots[-1]        # 逐位一致
        last = r1.snapshots[-1]
        qty = last.positions_qty.get(SYM)
        assert qty and qty > 0
        assert last.market_value == pytest.approx(qty * 10.2, rel=1e-12)

    def test_writeoff_nav_lower_by_exactly_residual(self):
        """判据 B：writeoff 的 NAV 比 carry **恰好低该标的残值**（手算校验）。"""
        r_carry = BTFRuntime().load_config(
            self._cfg_with_mode("carry")).build().run(persist=False)
        r_wo = BTFRuntime().load_config(
            self._cfg_with_mode("writeoff")).build().run(persist=False)

        carry_last = r_carry.snapshots[-1]
        residual = carry_last.market_value          # = qty × 10.2（残值）
        diff = carry_last.total_value - r_wo.snapshots[-1].total_value
        assert diff == pytest.approx(residual, rel=1e-12)
        assert r_wo.delisted_writeoff_amount == pytest.approx(residual,
                                                              rel=1e-12)
        # 末日快照：该标的市值 / 权重 / 数量"视同清零"；其余日路径不变
        wo_last = r_wo.snapshots[-1]
        assert SYM not in wo_last.positions_qty
        assert SYM not in wo_last.weights
        assert all(a == b for a, b in
                   zip(r_carry.snapshots[:-1], r_wo.snapshots[:-1],
                       strict=True))

    def test_writeoff_nav_four_consistent(self):
        """writeoff 的末日快照仍满足 NAV 四式（`audit_nav_four` 零漂移）。"""
        from btf.analytics.metrics import audit_nav_four

        r_wo = BTFRuntime().load_config(
            self._cfg_with_mode("writeoff")).build().run(persist=False)
        assert audit_nav_four(r_wo.snapshots) == []

    def test_writeoff_note_text(self):
        """判据 D：披露三要素保留 + 口径文字随取值（含出处与防前视声明）。"""
        rt = BTFRuntime().load_config(self._cfg_with_mode("writeoff")).build()
        rt.run(persist=False)
        joined = " | ".join(rt.assembly_notes)
        assert "退市持仓残值披露" in joined and SYM in joined
        assert D2.to_ymd() in joined                      # 末次交易日
        assert "按 0 核销" in joined and "压力档" in joined
        assert "§72.4" in joined                          # 口径出处
        assert "未取区间外价" in joined                   # 防前视声明

    def test_writeoff_at_delist_path_and_final_value(self):
        """K-2：三档对照——终值与 writeoff 相同，但末日不再假暴跌。"""
        from btf.analytics.metrics import audit_nav_four

        r_carry = BTFRuntime().load_config(
            self._cfg_with_mode("carry")).build().run(persist=False)
        r_wo = BTFRuntime().load_config(
            self._cfg_with_mode("writeoff")).build().run(persist=False)
        r_daily = BTFRuntime().load_config(
            self._cfg_with_mode("writeoff_at_delist")).build().run(persist=False)

        # 终值／核销额与 writeoff 逐位相同
        assert r_daily.snapshots[-1].total_value == pytest.approx(
            r_wo.snapshots[-1].total_value, rel=1e-12)
        assert r_daily.delisted_writeoff_amount == pytest.approx(
            r_wo.delisted_writeoff_amount, rel=1e-12)
        # 末日 daily_return 不再假暴跌（= carry 的无变动，而不是 writeoff 的一次性下挫）
        assert r_daily.snapshots[-1].daily_return == pytest.approx(
            r_carry.snapshots[-1].daily_return, rel=1e-12)
        assert r_daily.snapshots[-1].daily_return > r_wo.snapshots[-1].daily_return
        # D3 = 末次 bar（D2）的次一交易日：残值在这一天开始扣
        assert r_daily.snapshots[2].daily_return < r_carry.snapshots[2].daily_return
        assert SYM not in r_daily.snapshots[2].positions_qty
        # NAV 四式仍一致
        assert audit_nav_four(r_daily.snapshots) == []
        # 披露：逐标的起点 + 残值
        rt = BTFRuntime().load_config(
            self._cfg_with_mode("writeoff_at_delist")).build()
        rt.run(persist=False)
        joined = " | ".join(rt.assembly_notes)
        assert "writeoff_at_delist" in joined and D3.to_ymd() in joined
        assert r_daily.delisted_settlement_notes

    def test_cli_flag_end_to_end(self, delist_feed, tmp_path):
        """真入口：`bt run --delisted-settlement writeoff` ⇒ 退出 0；口径进
        config_effective（可追溯）＋ 披露落 manifest。"""
        import yaml
        from btf.cli.main import main

        path = tmp_path / "delist.yaml"
        path.write_text(yaml.safe_dump(self._cfg_with_mode(None),
                                       allow_unicode=True), encoding="utf-8")
        out_dir = tmp_path / "runs"
        assert main(["run", "--config", str(path), "--out", str(out_dir),
                     "--delisted-settlement", "writeoff"]) == 0
        run_dir = next(p for p in out_dir.iterdir() if p.is_dir())
        manifest = json.loads(
            (run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["config_effective"]["run"]["delisted_settlement"] \
            == "writeoff"
        assert any("按 0 核销" in n for n in manifest["assembly_notes"])
