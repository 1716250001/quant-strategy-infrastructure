# -*- coding: utf-8 -*-
"""registry 配置声明制单测（04 §8.5；M1 任务 4.2）。

六类扩展点注册冒烟 + 未知名显式报错 + 重名拒绝 + 程序化登记协商。
"""
from __future__ import annotations

from typing import ClassVar

import pytest
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION, ContractVersionError


class TestSixPointsSmoke:
    """六类扩展点：知名解析 + 实例化（配置声明制全链路）。"""

    SIX: ClassVar[list[tuple[str, str, dict]]] = [
        (registry.DATA_FEED, "memory", {"bars": []}),
        (registry.COST_MODEL, "zero", {}),
        (registry.SLIPPAGE_MODEL, "none", {}),
        (registry.EXECUTION_HANDLER, "next_open", {"instruments": {}}),
        (registry.REBALANCER, "full", {"instruments": {}}),
        (registry.RULES_PROVIDER, "static", {"params": {}}),
    ]

    @pytest.mark.parametrize("point,name,params", SIX)
    def test_create(self, point, name, params):
        plugin = registry.create(point, name, params)
        assert plugin is not None

    def test_flat_rate_cost_params(self):
        model = registry.create(registry.COST_MODEL, "flat_rate",
                                {"commission_rate": 3e-4})
        assert model.commission_rate == 3e-4


class TestUnknown:
    def test_unknown_plugin_name_lists_alternatives(self):
        with pytest.raises(registry.RegistryError, match="tushare_parquet"):
            registry.resolve(registry.DATA_FEED, "nonexistent_feed")

    def test_unknown_name_message_lists_available(self):
        with pytest.raises(registry.RegistryError) as e:
            registry.resolve(registry.COST_MODEL, "magic")
        assert "zero" in str(e.value) and "flat_rate" in str(e.value)

    def test_unknown_extension_point(self):
        with pytest.raises(registry.RegistryError, match="未知扩展点"):
            registry.resolve("time_machine", "flux")

    def test_unimplemented_point_names_error(self):
        """analyzer 待实现扩展点：未知名报错（含候选提示）。"""
        with pytest.raises(registry.RegistryError, match="未知插件名"):
            registry.resolve(registry.ANALYZER, "sharpe")


class TestRegister:
    def test_user_register_and_resolve(self):
        class _Feed:
            contract_version = CONTRACT_VERSION

        registry.register(registry.DATA_FEED, "unit_test_feed", _Feed)
        assert registry.resolve(registry.DATA_FEED, "unit_test_feed") is _Feed
        assert "unit_test_feed" in registry.available(registry.DATA_FEED)

    def test_duplicate_name_rejected(self):
        class _Feed2:
            contract_version = CONTRACT_VERSION

        with pytest.raises(registry.RegistryError, match="拒绝覆盖"):
            registry.register(registry.DATA_FEED, "memory", _Feed2)

    def test_register_requires_contract_version(self):
        class _NoVersion:
            pass

        with pytest.raises(ContractVersionError, match="未声明"):
            registry.register(registry.DATA_FEED, "unit_nover_feed", _NoVersion)

    def test_register_major_mismatch_rejected(self):
        class _V9:
            contract_version = "9.0"

        with pytest.raises(ContractVersionError, match="主版本不符"):
            registry.register(registry.DATA_FEED, "unit_v9_feed", _V9)
