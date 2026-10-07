# -*- coding: utf-8 -*-
"""L2 集成：`bt validate walk-forward|mc`（RC-15 / RC-16，2026-10-05）。

锚点（09 §14.10 #11/#12）：
- **RC-15**：窗口切分只用**交易日历**（此处置信于 Feed 自带 `dates`）；
  **训练/测试区间入 manifest**（折 run 的装配披露）；输出**含全部窗口**
  （folds 全量 + summary 带 min/max——不得只报最优）。
- **RC-16**：扰动只作用**收益序列层**（产物 snapshots/fills 逐位不变）；
  同种子重跑**逐位一致**；产物含 **p05/p50/p95** 与双侧 p 值；
  无基准 ⇒ 随机择时对照为 `null`（不伪造）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from btf import registry
from btf.config.paths import MARKET_DATA_DIR
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.experiment.store import LocalRunStore
from btf.runtime import BTFRuntime

from tests.fixtures.scenarios import make_bar, make_state, t_plus1_scenario

pytestmark = [pytest.mark.l2]

LONG_FEED = "rc15_long_feed"
MC_FEED = "rc16_mc_feed"
SYM = "000001.SZ"
N_DAYS = 60


def _long_days() -> list[TradingDate]:
    base = date(2015, 1, 5)
    return [TradingDate.from_ymd((base + timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(N_DAYS)]


@pytest.fixture(scope="module")
def long_feed():
    days = _long_days()
    bars, states = [], {}
    for i, d in enumerate(days):
        px = 10.0 + i * 0.01
        pre = 10.0 + max(0, i - 1) * 0.01
        bars.append(make_bar(SYM, d, px, px, px, px, pre))
        states[(d.to_ymd(), SYM)] = make_state(SYM, d, pre=pre)
    from btf.data.memory import MemoryFeed

    feed = MemoryFeed(bars, states,
                      {SYM: Instrument(SYM, AssetClass.STOCK, board="main",
                                       lot_size=100)}, dates=days)

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if LONG_FEED not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, LONG_FEED, _factory)
    return feed


def _write_cfg(tmp_path: Path, *, feed: str, days) -> Path:
    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": SYM, "weight": 0.9},
            "universe": {"source": "explicit", "symbols": [SYM]},
            "period": {"start": days[0].to_ymd(), "end": days[-1].to_ymd()},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": feed},
        "risk": {"rules": [], "allow_empty_chain": True},
    }
    path = tmp_path / "bt.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


class TestWalkForwardRc15:
    def test_cli_full_folds_and_summary(self, long_feed, tmp_path, capsys):
        from btf.cli.main import main

        days = _long_days()
        cfg_path = _write_cfg(tmp_path, feed=LONG_FEED, days=days)
        out_json = tmp_path / "wf.json"
        assert main(["validate", "walk-forward", "--config", str(cfg_path),
                     "--window", "20", "--test", "10", "--step", "10",
                     "--no-persist", "--out", str(out_json)]) == 0
        text = capsys.readouterr().out
        assert "4 折" in text and "OOS 汇总" in text
        assert "min=" in text and "max=" in text     # 不得只报最优

        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert report["n_folds"] == 4
        assert len(report["folds"]) == 4             # 全量折（含最差）
        assert report["spec"]["mode"] == "rolling"
        # 窗口切分来自**交易日历**（Feed dates——非自然月/非随机）
        assert report["folds"][0]["test"]["start"] == days[20].to_ymd()
        assert report["folds"][-1]["test"]["end"] == days[59].to_ymd()
        s = report["summary"]["sharpe_ratio"]
        assert s["n"] == 4 and s["min"] <= s["median"] <= s["max"]

    def test_anchored_mode(self, long_feed, tmp_path):
        from btf.cli.main import main

        days = _long_days()
        cfg_path = _write_cfg(tmp_path, feed=LONG_FEED, days=days)
        out_json = tmp_path / "wf_anchored.json"
        assert main(["validate", "walk-forward", "--config", str(cfg_path),
                     "--window", "20", "--test", "10", "--mode", "anchored",
                     "--no-persist", "--out", str(out_json)]) == 0
        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert report["folds"][1]["train"]["start"] == days[0].to_ymd()
        assert report["folds"][1]["train"]["end"] == days[29].to_ymd()

    def test_persisted_fold_manifest_carries_windows(self, long_feed, tmp_path,
                                                     capsys):
        """**训练/测试区间入 manifest**（09 #11）：折 run 的装配披露。"""
        from btf.cli.main import main

        days = _long_days()
        cfg_path = _write_cfg(tmp_path, feed=LONG_FEED, days=days)
        runs = tmp_path / "runs"
        assert main(["validate", "walk-forward", "--config", str(cfg_path),
                     "--window", "30", "--test", "15", "--step", "15",
                     "--out-dir", str(runs)]) == 0
        capsys.readouterr()
        dirs = [p for p in runs.iterdir() if p.is_dir()]
        assert len(dirs) == 2                        # 60 日装得下 2 折
        for d in dirs:
            manifest = json.loads(
                (d / "manifest.json").read_text(encoding="utf-8"))
            assert any("Walk-Forward 折" in n and "训练窗" in n
                       for n in manifest["assembly_notes"]), manifest["assembly_notes"]

    def test_insufficient_window_returns_one(self, long_feed, tmp_path, capsys):
        from btf.cli.main import main

        days = _long_days()
        cfg_path = _write_cfg(tmp_path, feed=LONG_FEED, days=days)
        assert main(["validate", "walk-forward", "--config", str(cfg_path),
                     "--window", "50", "--test", "20", "--no-persist"]) == 1
        assert "窗口" in capsys.readouterr().err


class TestWalkForwardRealCalendar:
    """真主库：窗口切分走 `trade_cal`（**非自然月**——折边界必为交易日）。

    检出过的真实缺陷：`calendar_days` 的参口径是 **YYYYMMDD**，而 period 是
    `YYYY-MM-DD`——不做转换会得到**空日历**（由本次交付的批 5 证据脚本实测
    抓出并修复；本用例是把该路径纳入常跑回归的守卫）。
    """

    @pytest.mark.skipif(not (Path(MARKET_DATA_DIR) / "daily").is_dir(),
                        reason=f"主库不可用: {MARKET_DATA_DIR}")
    def test_trade_cal_used_not_natural_months(self, tmp_path):
        from btf.cli.main import main

        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": SYM, "weight": 0.9},
                "universe": {"source": "explicit", "symbols": [SYM]},
                "period": {"start": "2024-01-02", "end": "2024-04-30"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": "tushare_parquet"},
            "risk": {"rules": [], "allow_empty_chain": True},
        }
        cfg_path = tmp_path / "wf_real.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True),
                            encoding="utf-8")
        out_json = tmp_path / "wf_real.json"
        assert main(["validate", "walk-forward", "--config", str(cfg_path),
                     "--window", "30", "--test", "15", "--step", "15",
                     "--no-persist", "--out", str(out_json)]) == 0
        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert report["n_folds"] >= 2, report["spec"]
        for fold in report["folds"]:
            for seg in ("train", "test"):
                for key in ("start", "end"):
                    ymd = fold[seg][key].replace("-", "")
                    day = TradingDate.from_ymd(ymd)
                    assert day.iso.weekday() < 5, fold    # 交易日（非周末）


class TestMonteCarloRc16:
    def _run_short(self, tmp_path: Path) -> tuple[LocalRunStore, str]:
        feed = t_plus1_scenario().feed

        def _factory(**_params):
            return feed

        _factory.contract_version = CONTRACT_VERSION
        if MC_FEED not in registry.available(registry.DATA_FEED):
            registry.register(registry.DATA_FEED, MC_FEED, _factory)
        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": SYM, "weight": 0.9},
                "universe": {"source": "explicit", "symbols": [SYM]},
                "period": {"start": "2015-01-05", "end": "2015-01-09"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": MC_FEED},
            "risk": {"rules": [], "allow_empty_chain": True},
        }
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(cfg).build()
        return store, rt.run(store=store).run_id

    def test_cli_mc_structure_and_determinism(self, tmp_path, capsys):
        from btf.cli.main import main

        _store, run_id = self._run_short(tmp_path)
        out1, out2 = tmp_path / "mc1.json", tmp_path / "mc2.json"
        base = ["validate", "mc", "--run", run_id, "--out-dir", str(tmp_path),
                "--n", "300"]
        assert main([*base, "--out", str(out1)]) == 0
        text = capsys.readouterr().out
        assert "p05=" in text and "p95=" in text and "跨0=" in text
        assert main([*base, "--out", str(out2)]) == 0

        rep1 = json.loads(out1.read_text(encoding="utf-8"))
        rep2 = json.loads(out2.read_text(encoding="utf-8"))
        assert rep1 == rep2                          # 同种子重跑**逐位一致**
        bs = rep1["bootstrap"]["sharpe"]
        for key in ("observed", "p05", "p50", "p95", "ci_low", "ci_high",
                    "crosses_zero"):
            assert key in bs, key
        assert rep1["permutation"]["sign_sharpe"]["p_value"] is not None
        assert rep1["permutation"]["order_max_drawdown"]["scheme"] == "order"
        # 无基准 ⇒ 随机择时对照 null（不伪造）
        assert rep1["noise_baseline"] is None
        assert "无基准" in rep1["noise_note"]
        assert rep1["seed"]["source"] == "manifest"  # seed.master 单源

    def test_run_artifacts_bit_identical_after_mc(self, tmp_path):
        """**撮合规则逐位不变**：MC 只读产物、不改任何成交/快照文件。"""
        from btf.cli.main import main

        store, run_id = self._run_short(tmp_path)
        run_dir = store.run_dir(run_id)

        def _hash(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        before = {name: _hash(run_dir / name)
                  for name in ("snapshots.jsonl", "fills.jsonl", "orders.jsonl")
                  if (run_dir / name).is_file()}
        assert main(["validate", "mc", "--run", run_id,
                     "--out-dir", str(tmp_path), "--n", "100"]) == 0
        after = {name: _hash(run_dir / name) for name in before}
        assert before == after

    def test_explicit_seed_recorded(self, tmp_path, capsys):
        from btf.cli.main import main

        _store, run_id = self._run_short(tmp_path)
        out = tmp_path / "mc_seed.json"
        assert main(["validate", "mc", "--run", run_id,
                     "--out-dir", str(tmp_path), "--n", "100",
                     "--seed", "42", "--out", str(out)]) == 0
        capsys.readouterr()
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["seed"]["used"] == 42
        assert rep["seed"]["source"] == "explicit"
