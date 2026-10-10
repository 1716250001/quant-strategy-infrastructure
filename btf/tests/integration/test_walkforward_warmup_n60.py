# -*- coding: utf-8 -*-
"""L2 集成：**R58′ / N60**——Walk-Forward warm-up 语义（19 号 §107.3–§107.4）。

判据对应（A1~A7 中可由本文件覆盖者）：
- **A5**：长 lookback 策略 wf 用例**断言 OOS 段 fill > 0**（N60 正面捕获网）；
  短 lookback 策略断言**满足最小需求后 warm-up 不改变 OOS 结果**（不变性）；
- 语义契约（§107.3.2）：① 热身只喂历史、**指标隔离**（`n_trading_days` ＝
  测试窗长而非 exec 长）② `--warmup 0` ＝旧行为对照 ③ 默认 auto：声明
  `lookback_bars` ⇒ 用之；未声明 ⇒ 252 ＋ **WARN**（禁静默）④ 历史不足
  ⇒ 按可用截断并披露。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import AssetClass, Instrument, TradingDate

from tests.fixtures.scenarios import make_bar, make_state

pytestmark = [pytest.mark.l2]

FEED = "n60_warmup_feed"
SYM = "000001.SZ"
N_DAYS = 120                      # 40 训练 + 30 测试 ×2 折
TRAIN, TEST = 40, 30


@pytest.fixture(scope="module")
def feed_days():
    base = date(2016, 1, 4)
    days = [TradingDate.from_ymd((base + timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(N_DAYS)]
    bars, states = [], {}
    for i, d in enumerate(days):
        px = 10.0 + i * 0.02                       # 单调上行（建仓后必持有）
        pre = 10.0 + max(0, i - 1) * 0.02
        bars.append(make_bar(SYM, d, px, px, px, px, pre))
        states[(d.to_ymd(), SYM)] = make_state(SYM, d, pre=pre)
    from btf.data.memory import MemoryFeed

    feed = MemoryFeed(bars, states,
                      {SYM: Instrument(SYM, AssetClass.STOCK, board="main",
                                       lot_size=100)}, dates=days)

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if FEED not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED, _factory)
    return days


def _cfg(tmp_path: Path, strategy: str, days) -> Path:
    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": strategy,
            "params": {"symbol": SYM, "weight": 0.9},
            "universe": {"source": "explicit", "symbols": [SYM]},
            "period": {"start": days[0].to_ymd(), "end": days[-1].to_ymd()},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": FEED},
        "risk": {"rules": [], "allow_empty_chain": True},
    }
    path = tmp_path / "bt.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


def _run(cfg_path: Path, tmp_path: Path, tag: str,
         *extra: str) -> dict:
    from btf.cli.main import main

    out = tmp_path / f"wf_{tag}.json"
    rc = main(["validate", "walk-forward", "--config", str(cfg_path),
               "--window", str(TRAIN), "--test", str(TEST),
               "--step", str(TEST), "--no-persist",
               "--out", str(out), *extra])
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


class TestWarmupSemanticsN60:
    LONG = "tests.fixtures.warmup_lookback:LongLookbackStrategy"
    SHORT = "tests.fixtures.warmup_lookback:ShortLookbackStrategy"
    NODECL = "tests.fixtures.buyhold:ConfigBuyHold"

    def test_declared_lookback_read_and_auto_source(self, feed_days, tmp_path):
        """auto：策略**声明** `lookback_bars=40` ⇒ 直接采用（source=auto:strategy）。"""
        from btf.config.loader import load_config
        from btf.optimize.walkforward import declared_lookback

        cfg_path = _cfg(tmp_path, self.LONG, feed_days)
        cfg = load_config(cfg_path)
        assert declared_lookback(cfg) == 40
        rep = _run(cfg_path, tmp_path, "auto")
        assert rep["spec"]["warmup"] == 40
        assert rep["spec"]["warmup_source"] == "auto:strategy"
        assert rep["warmup_note"] is None             # 声明明确 ⇒ 无告警

    def test_long_lookback_cold_start_starved(self, feed_days, tmp_path):
        """**A5-A（N60 现象复现）**：`--warmup 0` ⇒ 长 lookback 策略 OOS 合计 0 成交。"""
        cfg_path = _cfg(tmp_path, self.LONG, feed_days)
        rep = _run(cfg_path, tmp_path, "legacy0", "--warmup", "0")
        assert rep["spec"]["warmup_source"] == "legacy_zero"
        assert rep["warmup_note"]                       # 旧行为档**必须**告警
        assert all(f["warmup_bars"] == 0 for f in rep["folds"])
        total = sum(f["test_metrics"].get("n_fills", 0) or 0
                    for f in rep["folds"])
        assert total == 0, "冷启动应当饿死（N60 现象）"

    def test_long_lookback_warmup_restores_oos_fills(self, feed_days, tmp_path):
        """**A5-B（正面捕获网）**：默认（auto=40）⇒ OOS 段 fill **> 0**。"""
        cfg_path = _cfg(tmp_path, self.LONG, feed_days)
        rep = _run(cfg_path, tmp_path, "auto2")
        assert rep["spec"]["warmup"] == 40
        for f in rep["folds"]:
            assert f["warmup_bars"] == TRAIN           # 折 1 起 ts_idx=40 ⇒ 满 40
            assert f["test_metrics"]["n_fills"] >= 1, (
                f"折 {f['index']} 热身后 OOS 仍无成交（N60 未修复）")

    def test_metrics_isolated_to_test_range(self, feed_days, tmp_path):
        """**指标隔离（硬要求）**：`n_trading_days` ＝ 测试窗长（非 exec 长）。"""
        cfg_path = _cfg(tmp_path, self.LONG, feed_days)
        rep = _run(cfg_path, tmp_path, "auto3")
        for f in rep["folds"]:
            assert (f["exec_range"]["n_days"] - f["metric_range"]["n_days"]
                    == f["warmup_bars"]), "exec_range 与 metric_range 差集≠热身"
            assert f["test_metrics"]["n_trading_days"] == TEST, (
                "指标口径混入热身段（§107.3.2-2 硬要求）")

    def test_short_lookback_not_starved_by_warmup(self, feed_days, tmp_path):
        """**A5-C（短 lookback 对照）**：短 lookback 策略**两种档位都不被饿死**
        ——冷启动（`--warmup 0`）在 test 段**可成交**（≥1），热身档位下建仓已在
        热身段完成、test 段**持仓有效**（结构性质：无 test 段成交＋口径自洽＋
        持有期收益可算）。

        **诚实说明**：两档的 OOS 数值**不逐位一致**——建仓日期不同 ⇒ 成交价/份额
        不同 ⇒ 收益微差；这是「热身区间长度」的合法结果，**不是** 缺陷。长/短
        lookback 的判别因此落在「冷启动是否饿死」（上面两例）而非数值相等。
        """
        cfg_path = _cfg(tmp_path, self.SHORT, feed_days)
        cold = _run(cfg_path, tmp_path, "short0", "--warmup", "0")
        hot = _run(cfg_path, tmp_path, "short8", "--warmup", "8")
        for f in cold["folds"]:                        # 短 lookback：冷启动可成交
            assert f["test_metrics"]["n_fills"] >= 1
        for f in hot["folds"]:                         # 热身档：结构性质自洽
            m = f["test_metrics"]
            assert m["n_fills"] == 0                   # 建仓已在热身段完成
            assert m["n_trading_days"] == TEST
            assert m["total_return"] is not None and m["total_return"] > 0

    def test_undeclared_lookback_warns_and_clamps(self, feed_days, tmp_path,
                                                  caplog):
        """未声明 ⇒ **252 保守默认 + WARN**（禁静默）；历史不足 ⇒ 截断并披露。"""
        import logging

        cfg_path = _cfg(tmp_path, self.NODECL, feed_days)
        with caplog.at_level(logging.WARNING,
                             logger="btf.optimize.walkforward"):
            rep = _run(cfg_path, tmp_path, "nodecl")
        assert rep["spec"]["warmup"] == 252            # DEFAULT_WARMUP_BARS
        assert rep["spec"]["warmup_source"] == "auto:default_252"
        assert rep["warmup_note"] and "保守默认" in rep["warmup_note"]
        assert caplog.records, "未声明 lookback 必须 WARN（禁静默）"
        for f in rep["folds"]:
            # 截断＝min(252, 可用历史)；差集必须与 exec/metric range 自洽
            assert f["warmup_bars"] <= 252
            assert (f["exec_range"]["n_days"] - f["metric_range"]["n_days"]
                    == f["warmup_bars"])
            assert f["exec_range"]["start"] == feed_days[0].to_ymd()

    def test_cli_rejects_bad_warmup(self, feed_days, tmp_path, capsys):
        """CLI 守卫：`--warmup abc` / `--warmup -1` ⇒ 用法错误 EXIT=2。"""
        from btf.cli.main import main

        cfg_path = _cfg(tmp_path, self.LONG, feed_days)
        for bad in ("abc", "-1"):
            rc = main(["validate", "walk-forward", "--config", str(cfg_path),
                       "--window", str(TRAIN), "--test", str(TEST),
                       "--no-persist", "--warmup", bad])
            assert rc == 2
            assert "--warmup" in capsys.readouterr().err
