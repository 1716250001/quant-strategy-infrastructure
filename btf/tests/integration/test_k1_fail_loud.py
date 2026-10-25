# -*- coding: utf-8 -*-
"""§163 K-1 反向测试：P15 零成交 fail-loud／P17 feed 路由／C-3 判据一致性。"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import yaml
from btf import registry
from btf.config.validation import ConfigError
from btf.data.memory import MemoryFeed
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import AssetClass, Instrument
from btf.experiment.manifest import metrics_digest_of
from btf.experiment.store import LocalRunStore
from btf.runtime import BTFRuntime

from tests.fixtures.scenarios import t_plus1_scenario

pytestmark = [pytest.mark.l2]

RELATIVE_KEYS = (
    "benchmark_total_return", "excess_return", "annualized_excess",
    "tracking_error", "information_ratio", "beta", "alpha_annualized",
    "up_capture", "down_capture", "strategy_volatility_annualized",
)

P15_FEED = "k1_p15_feed"
EMPTY_FEED = "k1_empty_feed"


def _register(name: str, feed) -> None:
    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if name not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, name, _factory)


@pytest.fixture(scope="module", autouse=True)
def _feeds():
    _register(P15_FEED, t_plus1_scenario().feed)
    empty = MemoryFeed(
        [], {}, {"000001.SZ": Instrument(
            "000001.SZ", AssetClass.STOCK, board="main", lot_size=100)},
        dates=[])
    _register(EMPTY_FEED, empty)


def _p15_cfg(cash: float) -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": "2015-01-05", "end": "2015-01-09"},
            "initial_cash": cash,
        },
        "data": {"feed": P15_FEED},
        "report": {"benchmark": "000300.SH"},
        "risk": {"rules": [], "allow_empty_chain": True},
    }


class TestP15EmptyRun:
    def test_zero_fill_omits_relative_metrics_and_manifest_flag(self, tmp_path):
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(_p15_cfg(100.0)).build()
        result = rt.run(store=store)
        assert len(result.fills) == 0
        assert result.manifest.is_empty is True
        missing = [k for k in RELATIVE_KEYS if k in result.metrics]
        assert not missing, f"零成交仍给出基准相对项：{missing}"
        assert "total_return" in result.metrics, "基础指标必须保留"

    def test_zero_fill_cli_warns(self, tmp_path, capsys):
        from btf.cli.main import main

        path = tmp_path / "empty.yaml"
        path.write_text(yaml.safe_dump(_p15_cfg(100.0), allow_unicode=True),
                        encoding="utf-8")
        assert main(["run", "--config", str(path),
                     "--out", str(tmp_path / "runs")]) == 0
        out = capsys.readouterr().out
        assert "本次运行零成交" in out and "n_fills=0" in out

    def test_filled_run_keeps_relative_metrics_and_digest(self, tmp_path):
        store = LocalRunStore(tmp_path)
        r1 = BTFRuntime().load_config(_p15_cfg(1_000_000.0)).build().run(
            store=store)
        r2 = BTFRuntime().load_config(_p15_cfg(1_000_000.0)).build().run(
            store=store)
        assert len(r1.fills) > 0
        assert all(k in r1.metrics for k in RELATIVE_KEYS)
        assert metrics_digest_of(r1.metrics) == metrics_digest_of(r2.metrics)


class TestP17FeedRouting:
    def _cfg(self, feed: str) -> dict:
        return {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "510210.SH", "weight": 0.9},
                "universe": {"source": "explicit", "symbols": ["510210.SH"]},
                "period": {"start": "2022-01-04", "end": "2022-01-31"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": feed},
            "risk": {"rules": [], "allow_empty_chain": True},
        }

    def test_etf_with_parquet_feed_is_config_error(self):
        with pytest.raises(ConfigError, match=r"510210\.SH"):
            BTFRuntime().load_config(self._cfg("tushare_parquet")).build()

    def test_same_etf_with_mixed_feed_runs(self, tmp_path):
        rt = BTFRuntime().load_config(self._cfg("mixed")).build()
        result = rt.run(store=LocalRunStore(tmp_path))
        assert result.n_days > 0
        assert "total_return" in result.metrics


class TestC3ZeroDayWarnings:
    def test_api_zero_day_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="btf.runtime"):
            BTFRuntime._warn_if_short_period(SimpleNamespace(n_days=0))
        assert "未读到任何交易日" in " ".join(
            r.getMessage() for r in caplog.records)

    def test_cli_zero_day_warning(self, tmp_path, capsys):
        from btf.cli.main import main

        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "000001.SZ", "weight": 0.9},
                "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
                "period": {"start": "2015-01-05", "end": "2015-01-09"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": EMPTY_FEED},
            "risk": {"rules": [], "allow_empty_chain": True},
        }
        path = tmp_path / "zero_day.yaml"
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        assert main(["run", "--config", str(path),
                     "--out", str(tmp_path / "runs")]) == 0
        out = capsys.readouterr().out
        assert "未读到任何交易日" in out
