# -*- coding: utf-8 -*-
"""L2 集成：RC-14（2026-10-05）数据版本门禁（`data.dataset.version` 复活）。

- **声明键归位**：`data.dataset.{version,expected_hash,anchor_date}`（schema 现
  可达；原实现读顶层 `data.anchor_date/expected_hash`——在
  `additionalProperties: false` 下**永远无法合法传入** ⇒ 死键）。
- **门禁**（fail-closed）：声明哈希 ⇒ 装配期实算并严格比对；声明水位 ⇒
  实算水位 ≥ 声明；内存 Feed 声明任何版本键 ⇒ 拒（无法校验）。
- **opt-in 零成本**：未声明 ⇒ 不计算、不披露、行为逐位不变。
"""
from __future__ import annotations

import pytest
from btf import registry
from btf.config.paths import MARKET_DATA_DIR
from btf.config.validation import ConfigError
from btf.domain.contracts import CONTRACT_VERSION

from tests.fixtures.scenarios import t_plus1_scenario

pytestmark = [pytest.mark.l2]

ROOT = MARKET_DATA_DIR
requires_mainlib = pytest.mark.skipif(
    not (ROOT / "daily").is_dir(), reason=f"主库不可用: {ROOT}")

MEM_FEED = "rc14_gate_mem_feed"


# ─────────────────────────────────────────────────────────────
# 一、声明键归位（单元）
# ─────────────────────────────────────────────────────────────
class TestCollectDataVersionLocation:
    def test_reads_dataset_block(self):
        from btf.experiment.manifest import collect_data_version

        dv = collect_data_version({"data": {
            "feed": "tushare_parquet",
            "dataset": {"anchor_date": "2024-12-31",
                        "expected_hash": "sha256:abc"}}})
        assert dv["anchor_date"] == "2024-12-31"
        assert dv["content_hash"] == "sha256:abc"

    def test_top_level_fallback_kept(self):
        """兼容旧位（历史产物 / 直调）：顶层仍可读，`dataset.*` 优先。"""
        from btf.experiment.manifest import collect_data_version

        dv = collect_data_version({"data": {
            "feed": "memory", "expected_hash": "sha256:old"}})
        assert dv["content_hash"] == "sha256:old"
        dv2 = collect_data_version({"data": {
            "feed": "memory", "expected_hash": "sha256:old",
            "dataset": {"expected_hash": "sha256:new"}}})
        assert dv2["content_hash"] == "sha256:new"


# ─────────────────────────────────────────────────────────────
# 二、门禁（内存 Feed：声明即拒）
# ─────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def mem_feed():
    feed = t_plus1_scenario().feed

    def factory(**_params):
        return feed

    factory.contract_version = CONTRACT_VERSION
    if MEM_FEED not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, MEM_FEED, factory)
    return feed


def _mem_cfg(**dataset) -> dict:
    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": "2015-01-05", "end": "2015-01-09"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": MEM_FEED},
        "risk": {"rules": [], "allow_empty_chain": True},
    }
    if dataset:
        cfg["data"]["dataset"] = dataset
    return cfg


class TestMemoryFeedDeclaredRejected:
    def test_declared_on_memory_feed_rejected(self, mem_feed):
        """内存 Feed 无文件指纹 ⇒ 声明版本键即拒（拒绝静默放行）。"""
        from btf.runtime import BTFRuntime

        with pytest.raises(ConfigError, match="内存 Feed"):
            BTFRuntime().load_config(
                _mem_cfg(version="sha256:whatever")).build()

    def test_undeclared_memory_feed_unchanged(self, mem_feed):
        """opt-in 零成本：未声明 ⇒ 不计算、不披露（行为逐位不变）。"""
        from btf.runtime import BTFRuntime

        rt = BTFRuntime().load_config(_mem_cfg()).build()
        assert not any("数据版本门禁" in n for n in rt.assembly_notes)
        assert rt.run(persist=False).n_days == 5


# ─────────────────────────────────────────────────────────────
# 三、门禁（文件 Feed：一致性 / 水位）
# ─────────────────────────────────────────────────────────────
def _lib_cfg(dataset: dict | None = None) -> dict:
    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": "2024-01-02", "end": "2024-01-05"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": "tushare_parquet"},
        "risk": {"rules": [], "allow_empty_chain": True},
    }
    if dataset:
        cfg["data"]["dataset"] = dataset
    return cfg


def _probe_hash() -> str:
    """先不带声明 build 一次 → 取实算 content_hash（门禁的真值来源）。"""
    from btf.runtime import BTFRuntime

    rt = BTFRuntime().load_config(_lib_cfg()).build()
    return str(rt._compute_data_version(rt.config["run"]["period"])["content_hash"])


@requires_mainlib
class TestFileFeedGate:
    def test_hash_match_passes_with_disclosure(self):
        """声明真值 ⇒ 通过 + 装配披露（声明 vs 实算 + 档位）。"""
        from btf.runtime import BTFRuntime

        truth = _probe_hash()
        rt = BTFRuntime().load_config(_lib_cfg({"version": truth})).build()
        joined = " | ".join(rt.assembly_notes)
        assert "数据版本门禁通过" in joined
        assert truth in joined and "档位" in joined

    def test_hash_mismatch_rejected(self):
        """声明错哈希 ⇒ ConfigError（拒绝静默换数）。"""
        from btf.runtime import BTFRuntime

        with pytest.raises(ConfigError, match="数据版本门禁未通过"):
            BTFRuntime().load_config(
                _lib_cfg({"version": "sha256:deadbeef"})).build()

    def test_version_and_expected_hash_conflict_rejected(self):
        """version 与 expected_hash 同时声明且不一致 ⇒ 自相矛盾即拒。"""
        from btf.runtime import BTFRuntime

        with pytest.raises(ConfigError, match="自相矛盾"):
            BTFRuntime().load_config(_lib_cfg({
                "version": "sha256:aaa", "expected_hash": "sha256:bbb"})).build()

    def test_anchor_beyond_watermark_rejected(self):
        """声明水位 ≥ 实算水位下限（2099 不可能满足）⇒ 拒。"""
        from btf.runtime import BTFRuntime

        with pytest.raises(ConfigError, match="数据水位"):
            BTFRuntime().load_config(
                _lib_cfg({"anchor_date": "2099-12-31"})).build()

    def test_anchor_within_watermark_passes(self):
        """声明水位 ≤ 实算 ⇒ 通过（数据已更新到声明水位之上）。"""
        from btf.runtime import BTFRuntime

        rt = BTFRuntime().load_config(
            _lib_cfg({"anchor_date": "2020-01-01"})).build()
        assert any("数据版本门禁通过" in n for n in rt.assembly_notes)
