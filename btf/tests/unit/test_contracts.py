# -*- coding: utf-8 -*-
"""S1 契约版本协商单测（04 §8.7；M1 任务 4.1）。

主版本不符 → 拒载（不静默降级）；未声明 / 非法格式 → 显式报错。
"""
from __future__ import annotations

import pytest
from btf.domain.contracts import (
    CONTRACT_VERSION,
    ContractVersionError,
    major_of,
    negotiate,
)


class TestNegotiate:
    def test_host_version_shape(self):
        assert major_of(CONTRACT_VERSION) == 1

    def test_match_passes(self):
        negotiate("p", "1.0")

    def test_minor_patch_drift_passes(self):
        negotiate("p", "1.4.2")
        negotiate("p", "1.0.99")

    def test_major_mismatch_rejected(self):
        with pytest.raises(ContractVersionError, match="主版本不符"):
            negotiate("p", "2.0")

    def test_major_mismatch_message_names_plugin(self):
        with pytest.raises(ContractVersionError, match="'plugin_x'"):
            negotiate("plugin_x", "3.1")

    def test_undeclared_rejected(self):
        with pytest.raises(ContractVersionError, match="未声明"):
            negotiate("p", None)
        with pytest.raises(ContractVersionError, match="未声明"):
            negotiate("p", "")

    def test_invalid_format_rejected(self):
        with pytest.raises(ContractVersionError, match="非法"):
            negotiate("p", "abc")


class TestRegistryPluginsDeclared:
    """全部内置插件必须声明契约版本且主版本相符（协商联动冒烟）。"""

    def test_builtin_plugins_carry_contract_version(self):
        from btf import registry

        checked = 0
        for point in registry.points():
            for name in registry.available(point):
                factory = registry.resolve(point, name)   # 协商在此发生
                assert getattr(factory, "contract_version", None), \
                    f"{point}:{name} 未声明 contract_version"
                checked += 1
        assert checked >= 7   # 六类扩展点冒烟 + 实现类版本声明
