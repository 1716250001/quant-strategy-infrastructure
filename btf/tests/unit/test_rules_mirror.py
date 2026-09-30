# -*- coding: utf-8 -*-
"""规则镜像与 MirrorJsonRulesProvider 单测（M3 任务 6.1；04 §8.3.7 H2）。

覆盖：
    1. `MirrorJsonRulesProvider`：指纹/取参/aliases 文档键名/未登记报错/
       overrides 留痕/单位口径显式；
    2. registry RULES_PROVIDER 注册 `mirror_json` + 版本协商；
    3. runtime 装配：source=mirror_json → 提供者就位；path 缺失 → ConfigError；
       override 进 manifest（H2 覆盖即留痕）；
    4. `export_rules_mirror`：载荷幂等 + 指纹内容敏感 + 单位/别名声明。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
from btf import registry
from btf.registry import RULES_PROVIDER
from btf.strategy.rules import MirrorJsonRulesProvider, StaticRulesProvider

import export_rules_mirror as exporter

pytestmark = [pytest.mark.l1]

MIRROR_JSON = Path(r"D:\量化策略\赤潮\rules_mirror_v77.json")

V75 = {"pe_max": 30.0, "pb_max": 3.0, "roe_min": 5.0, "dv_min": 1.0}
LIQ = {"sh_crisis": -5.0, "sh_watch": -3.0, "small_crisis": -6.0,
       "small_watch": -4.0, "down_crisis": 800, "down_watch": 300,
       "ratio_crisis": 10.0, "ratio_watch": 5.0}


@pytest.fixture()
def mirror_path(tmp_path) -> Path:
    payload = exporter.build_payload(V75, LIQ)
    path = tmp_path / "rules_mirror_v77.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


class TestMirrorJsonRulesProvider:
    def test_rules_version_fingerprint(self, mirror_path):
        provider = MirrorJsonRulesProvider(mirror_path)
        assert provider.rules_version().startswith("v7.7@sha256:")

    def test_get_l2_filter(self, mirror_path):
        provider = MirrorJsonRulesProvider(mirror_path)
        assert provider.get("L2_filter") == V75

    def test_get_liq(self, mirror_path):
        assert MirrorJsonRulesProvider(mirror_path).get("L0_LIQ") == LIQ

    def test_alias_declared_in_mirror(self, mirror_path):
        """aliases 声明文档键名 → 镜像键名（04 §8.3.7 与 md_core 口径桥接）。"""
        provider = MirrorJsonRulesProvider(mirror_path)
        assert provider._aliases["pe_ttm_max"] == "pe_max"
        assert provider.get("L2_filter")["pe_max"] == 30.0

    def test_unknown_rule_raises(self, mirror_path):
        with pytest.raises(KeyError, match="未登记规则"):
            MirrorJsonRulesProvider(mirror_path).get("L9_unknown")

    def test_overrides_recorded(self, mirror_path):
        overrides = ({"rule_id": "L2_filter", "param": "pe_max", "value": 20},)
        provider = MirrorJsonRulesProvider(mirror_path, overrides=overrides)
        assert provider.overrides == overrides

    def test_units_declared(self, mirror_path):
        units = MirrorJsonRulesProvider(mirror_path).units()
        assert units["roe_min"] == "percent"      # 百分数口径显式（防双口径）
        assert units["pe_max"] == "ratio"

    def test_rule_ids(self, mirror_path):
        assert MirrorJsonRulesProvider(mirror_path).rule_ids == ["L0_LIQ",
                                                                 "L2_filter"]

    def test_missing_file(self, tmp_path):
        with pytest.raises(OSError):
            MirrorJsonRulesProvider(tmp_path / "nope.json")


class TestRegistry:
    def test_registered(self):
        assert "mirror_json" in registry.available(RULES_PROVIDER)
        assert "static" in registry.available(RULES_PROVIDER)

    def test_create_and_contract(self, mirror_path):
        provider = registry.create(RULES_PROVIDER, "mirror_json",
                                   {"path": str(mirror_path)})
        assert isinstance(provider, MirrorJsonRulesProvider)
        assert provider.contract_version == "1.0"

    def test_unknown_source(self):
        with pytest.raises(registry.RegistryError, match="未知插件名"):
            registry.create(RULES_PROVIDER, "yaml_literal")


class TestRuntimeWiring:
    """runtime 装配（feed 用 MemoryFeed 注入，与纵切集成测试同套路）。"""

    CONFIG: ClassVar[dict] = {
        "schema_version": "backtest.v1",
        "run": {"strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "000001.SZ", "weight": 0.9},
                "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
                "period": {"start": "2015-01-05", "end": "2015-01-09"},
                "initial_cash": 1_000_000},
        "data": {"feed": "memory"},
        # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
        "risk": {"rules": [], "allow_empty_chain": True},
    }

    @pytest.fixture(autouse=True)
    def _feed(self):
        """注册 MemoryFeed（data.feed=memory 需构造参数，故以工厂注入）。"""
        from btf.domain.contracts import CONTRACT_VERSION

        from tests.fixtures.scenarios import t_plus1_scenario

        feed = t_plus1_scenario().feed

        def factory(**_params):
            return feed

        factory.contract_version = CONTRACT_VERSION
        name = "rules_mirror_feed"
        if name not in registry.available(registry.DATA_FEED):
            registry.register(registry.DATA_FEED, name, factory)
        self.CONFIG = {**TestRuntimeWiring.CONFIG,
                       "data": {"feed": name}}

    def test_mirror_json_assembled(self, mirror_path):
        from btf.runtime import BTFRuntime

        cfg = {**self.CONFIG,
               "rules": {"source": "mirror_json", "path": str(mirror_path)}}
        rt = BTFRuntime().load_config(cfg).build()
        assert isinstance(rt.rules_provider, MirrorJsonRulesProvider)
        assert rt.rules_provider.rules_version().startswith("v7.7@")

    def test_missing_path_rejected(self):
        from btf.config.validation import ConfigError
        from btf.runtime import BTFRuntime

        cfg = {**self.CONFIG, "rules": {"source": "mirror_json"}}
        with pytest.raises(ConfigError, match=r"rules\.path"):
            BTFRuntime().load_config(cfg).build()

    def test_static_still_supported(self):
        from btf.runtime import BTFRuntime

        cfg = {**self.CONFIG, "rules": {"source": "static"}}
        rt = BTFRuntime().load_config(cfg).build()
        assert isinstance(rt.rules_provider, StaticRulesProvider)


class TestExportPayload:
    def test_payload_idempotent(self):
        a = exporter.build_payload(V75, LIQ)
        b = exporter.build_payload(V75, LIQ)
        assert a == b                                  # 不含时间戳 → 幂等

    def test_fingerprint_changes_with_content(self):
        base = exporter.build_payload(V75, LIQ)["rules_version"]
        changed = exporter.build_payload({**V75, "pe_max": 25.0}, LIQ)
        assert changed["rules_version"] != base

    def test_units_and_aliases_declared(self):
        payload = exporter.build_payload(V75, LIQ)
        assert payload["units"]["roe_min"] == "percent"
        assert payload["aliases"]["pe_ttm_max"] == "pe_max"
        assert payload["schema_version"] == "rules_mirror.v1"

    def test_digest_is_deterministic_and_sensitive(self):
        rules = exporter.build_payload(V75, LIQ)["rules"]
        assert exporter.digest_of(rules) == exporter.digest_of(rules)
        assert exporter.digest_of(rules) != exporter.digest_of(
            {"L2_filter": V75})
