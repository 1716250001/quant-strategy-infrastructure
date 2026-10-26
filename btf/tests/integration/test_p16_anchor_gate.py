# -*- coding: utf-8 -*-
"""L2 集成：**P16（19 号 §165）**数据水位口径。

水位 = **行情表**（`daily`/`fund_daily`/`index_daily`）`date_max` 的 **min**，
并**双向夹取区间末**。反向用例锁三件事：

  ① `stock_basic.date_max`（实测 20260930＝最新上市日）**不得**掩盖行情表滞后；
  ② **真实滞后**（行情表 20240103 < 声明 20240104，且声明 ≤ 区间末）仍照红；
  ③ 声明超区间末（跑 2016 声明 2026-09-18 那一类）夹取后**必须通过**。

另锁 **D**：`content_hash`（`fp.digest`）逐字不变——P16 只动锚点语义。
"""
from __future__ import annotations

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.config.validation import ConfigError

pytestmark = [pytest.mark.l2]

requires_mainlib = pytest.mark.skipif(
    not (MARKET_DATA_DIR / "daily").is_dir(), reason=f"主库不可用: {MARKET_DATA_DIR}")


class _FakeFingerprint:
    """最小指纹替身（只暴露 `digest` / `as_dict()`——门禁用到的两个口）。"""

    digest = "sha256:p16-fake-hash"

    def __init__(self, detail: dict):
        self._detail = detail

    def as_dict(self) -> dict:
        return self._detail


def _cfg(*, anchor: str | None = None, end: str = "2024-01-05") -> dict:
    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": "2024-01-02", "end": end},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": "tushare_parquet"},
        "risk": {"rules": [], "allow_empty_chain": True},
    }
    if anchor:
        cfg["data"]["dataset"] = {"anchor_date": anchor}
    return cfg


@pytest.fixture(autouse=True)
def _clear_fingerprint_memo():
    """指纹 memo 是**进程级**：每个用例前清掉，避免真实指纹串味到替身。"""
    from btf.runtime import component_assembler as ca

    ca._FINGERPRINT_MEMO.clear()
    yield
    ca._FINGERPRINT_MEMO.clear()


def _install_fake(monkeypatch, detail: dict) -> None:
    from btf.data import version as version_mod

    monkeypatch.setattr(version_mod, "compute",
                        lambda *a, **k: _FakeFingerprint(detail))


# 行情表 20240103 已滞后于区间末（20240105）；`stock_basic` 更"新"（20260930）。
_LAGGING = {
    "daily": {"date_max": "20240103"},
    "index_daily": {"date_max": "20240104"},
    "stock_basic": {"date_max": "20260930"},
    "adj_factor": {"date_max": "20240105"},
}


class TestAnchorFormula:
    def test_anchor_is_min_of_price_tables_and_ignores_stock_basic(self, monkeypatch):
        """① 锚点 = min(daily, index_daily) —— `stock_basic`（20260930）不参与。"""
        from btf.runtime import BTFRuntime

        _install_fake(monkeypatch, _LAGGING)
        rt = BTFRuntime().load_config(_cfg()).build()
        fp = rt.dataset_fingerprint()
        assert fp["anchor_date"] == "20240103", fp
        assert fp["anchor_date"] != "20260930"

    def test_anchor_clamped_to_period_end(self, monkeypatch):
        """③ 行情表水位在区间之后 ⇒ 锚点夹取区间末（不是未来日期）。"""
        from btf.runtime import BTFRuntime

        _install_fake(monkeypatch, {"daily": {"date_max": "20260930"}})
        rt = BTFRuntime().load_config(_cfg()).build()
        assert rt.dataset_fingerprint()["anchor_date"] == "20240105"

    def test_content_hash_unchanged(self, monkeypatch):
        """D：`content_hash` 仍取 `fp.digest`（P16 不得改哈希口径）。"""
        from btf.runtime import BTFRuntime

        _install_fake(monkeypatch, _LAGGING)
        rt = BTFRuntime().load_config(_cfg()).build()
        assert rt.dataset_fingerprint()["content_hash"] == "sha256:p16-fake-hash"


class TestLagStillRed:
    def test_true_lag_rejected(self, monkeypatch):
        """② 真滞后：min 行情水位 20240103 < 声明 20240104 ⇒ 拒。"""
        from btf.runtime import BTFRuntime

        _install_fake(monkeypatch, _LAGGING)
        with pytest.raises(ConfigError, match=r"行情表数据水位 20240103"):
            BTFRuntime().load_config(_cfg(anchor="2024-01-04")).build()

    def test_stock_basic_cannot_mask_lag(self, monkeypatch):
        """①+② `stock_basic`（20260930）再新也救不了滞后行情表 ⇒ 仍拒。"""
        from btf.runtime import BTFRuntime

        _install_fake(monkeypatch, _LAGGING)
        with pytest.raises(ConfigError, match=r"数据版本门禁未通过"):
            BTFRuntime().load_config(_cfg(anchor="2024-01-05")).build()

    def test_declared_equal_to_lagging_anchor_passes(self, monkeypatch):
        """声明恰等于真水位 ⇒ 通过（下限语义，不是严格大于）。"""
        from btf.runtime import BTFRuntime

        _install_fake(monkeypatch, _LAGGING)
        rt = BTFRuntime().load_config(_cfg(anchor="2024-01-03")).build()
        assert any("数据版本门禁通过" in n for n in rt.assembly_notes)

    def test_declared_beyond_period_end_clamped_passes(self, monkeypatch):
        """③ 声明超区间末 ⇒ 夹取到区间末后通过（不误伤历史配置）。"""
        from btf.runtime import BTFRuntime

        _install_fake(monkeypatch, {"daily": {"date_max": "20240105"}})
        rt = BTFRuntime().load_config(
            _cfg(anchor="2026-09-18", end="2024-01-05")).build()
        joined = " | ".join(rt.assembly_notes)
        assert "数据版本门禁通过" in joined
        assert "夹取" in joined
        assert rt.dataset_fingerprint()["anchor_date"] == "20240105"


@requires_mainlib
class TestRealLibraryAnchor:
    def test_default_period_anchor_is_not_stock_basic_date(self):
        """实库：正常区间（数据已覆盖）锚点 == 区间末，且 ≠ stock_basic 日期。"""
        from btf.runtime import BTFRuntime

        rt = BTFRuntime().load_config(_cfg()).build()
        fp = rt.dataset_fingerprint()
        assert fp["anchor_date"] == "20240105", fp
        detail = fp["tables_detail"]
        if "stock_basic" in detail and detail["stock_basic"].get("date_max"):
            assert fp["anchor_date"] != detail["stock_basic"]["date_max"]
