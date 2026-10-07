# -*- coding: utf-8 -*-
"""L2 集成：成本模型参数可配（**RC-05，2026-10-05**）。

原缺陷：schema `execution` 段**无** `cost_model_params` 键（`additionalProperties:
false` 会直接拒），且 `_runtime` 调 `registry.create(...)` **不传 params**
⇒「把成本翻倍跑一遍」（业界最低要求的成本压力测试）只能靠写插件代码。

本文件同时覆盖 `zero` 模型的参数忽略语义（零费用模型无字段，传参会 TypeError
⇒ 显式忽略 + warn，不静默）。
"""
from __future__ import annotations

import pytest
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.orders import OrderSide
from btf.domain.types import TradingDate
from btf.runtime import BTFRuntime

from tests.fixtures.scenarios import t_plus1_scenario

pytestmark = [pytest.mark.l2]

FEED_NAME = "rc05_feed"


@pytest.fixture()
def feed_registered():
    feed = t_plus1_scenario().feed

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, _factory)
    return feed


def _build(feed_registered, **execution):
    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": "2015-01-05", "end": "2015-01-09"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": FEED_NAME},
        "risk": {"rules": [{"name": "cash_check"}]},
        "execution": execution,
    }
    return BTFRuntime().load_config(cfg).build()


class TestCostModelParams:
    def test_commission_rate_injected(self, feed_registered):
        rt = _build(feed_registered, cost_model="tiered_v1",
                    cost_model_params={"commission_rate": 0.0005})
        assert rt.cost_model.commission_rate == pytest.approx(0.0005)
        # 参数进生效配置（可复现/可披露）
        assert rt.config["execution"]["cost_model_params"] == {
            "commission_rate": 0.0005}

    def test_default_unchanged(self, feed_registered):
        """不声明 ⇒ 模型自身默认（万 2.5）——零回归。"""
        rt = _build(feed_registered, cost_model="tiered_v1")
        assert rt.cost_model.commission_rate == pytest.approx(2.5e-4)

    def test_flat_rate_variant(self, feed_registered):
        rt = _build(feed_registered, cost_model="flat_rate",
                    cost_model_params={"commission_rate": 0.001})
        assert rt.cost_model.commission_rate == pytest.approx(0.001)

    def test_fee_doubling_observable(self, feed_registered):
        """成本压力测试最小形态：佣金翻倍 ⇒ 费用可观测上升（同口径可对照）。"""
        base = _build(feed_registered, cost_model="flat_rate")
        doubled = _build(feed_registered, cost_model="flat_rate",
                         cost_model_params={"commission_rate": 0.0005})
        date = TradingDate.from_ymd("20150106")
        f1 = base.cost_model.fees("000001.SZ", OrderSide.BUY, 10_000, 10.0, date)
        f2 = doubled.cost_model.fees("000001.SZ", OrderSide.BUY, 10_000, 10.0,
                                     date)
        assert f2.commission > f1.commission

    def test_zero_ignores_params_with_warning(self, feed_registered, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="btf.runtime._runtime"):
            rt = _build(feed_registered, cost_model="zero",
                        cost_model_params={"commission_rate": 0.0005})
        assert type(rt.cost_model).__name__ == "ZeroCostModel"
        assert any("忽略 cost_model_params" in r.getMessage()
                   for r in caplog.records)

    def test_schema_rejects_unknown_cost_param_key(self, feed_registered):
        """未登记键仍按 `additionalProperties:false` 拒（不放开 schema 口径）。"""
        from btf.config.validation import ConfigError, validate

        rt = _build(feed_registered, cost_model="tiered_v1")
        bad = {**rt.config, "execution": {"cost_model": "tiered_v1",
                                          "cost_model_params_x": {}}}
        with pytest.raises(ConfigError):
            validate(bad)
