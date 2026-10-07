# -*- coding: utf-8 -*-
"""L1 单测：RC-13（2026-10-05）危机区间清单外置 + 装配披露。

- 常量表：5 窗（对照审查 P1-5 原文清单：2015/2018/2020/2022/2024）；
- `intersect`：闭区间重叠判定（窗内 / 窗外 / 十年全命中）；
- `from_config`：`analysis.crisis_windows` 覆盖 + 非法即 `ValueError`（fail-closed）；
- 运行时：重叠 ⇒ 装配披露出现（含窗名）；不重叠 ⇒ **不得出现**（双向探针）。
"""
from __future__ import annotations

import pytest
from btf import registry
from btf.analytics.windows import (
    CRISIS_WINDOWS,
    CrisisWindow,
    from_config,
    intersect,
)
from btf.domain.contracts import CONTRACT_VERSION

from tests.fixtures.scenarios import t_plus1_scenario

pytestmark = [pytest.mark.l1]

FEED_NAME = "rc13_windows_feed"


class TestConstants:
    def test_five_builtin_windows(self):
        assert len(CRISIS_WINDOWS) == 5
        names = " | ".join(w.name for w in CRISIS_WINDOWS)
        for key in ("2015", "2018", "2020", "2022", "2024"):
            assert key in names

    def test_intersect_hits_and_misses(self):
        assert intersect("2015-07-01", "2015-07-31")            # 股灾窗内
        assert intersect("2015-01-05", "2015-01-09") == ()      # 窗外
        assert len(intersect("2015-01-01", "2025-12-31")) == 5  # 十年全命中
        assert intersect("2019-01-01", "2019-12-31") == ()      # 平静期

    def test_overlap_boundary_closed(self):
        w = CrisisWindow("t", "2015-06-01", "2015-06-30")
        assert w.overlaps("2015-06-01", "2015-06-01")           # 闭区间
        assert w.overlaps("2015-05-01", "2015-06-01")
        assert not w.overlaps("2015-05-01", "2015-05-31")


class TestFromConfig:
    def test_override(self):
        cfg = {"analysis": {"crisis_windows": [
            {"name": "测试窗", "start": "2026-01-01", "end": "2026-01-31"}]}}
        assert from_config(cfg) == (
            CrisisWindow("测试窗", "2026-01-01", "2026-01-31"),)

    def test_default_when_absent(self):
        assert from_config(None) == CRISIS_WINDOWS
        assert from_config({}) == CRISIS_WINDOWS

    def test_invalid_rejected(self):
        with pytest.raises(ValueError, match="crisis_windows"):
            from_config({"analysis": {"crisis_windows": [
                {"name": "x", "start": "2015/06/01", "end": "2015-07-01"}]}})
        with pytest.raises(ValueError, match="crisis_windows"):
            from_config({"analysis": {"crisis_windows": [
                {"name": "x", "start": "2015-07-01", "end": "2015-06-01"}]}})


class TestRuntimeDisclosure:
    """装配披露双向探针（重叠必现 / 不重叠必不现）。"""

    def _build(self, *, analysis: dict | None = None):
        from btf.runtime import BTFRuntime

        feed = t_plus1_scenario().feed

        def factory(**_params):
            return feed

        factory.contract_version = CONTRACT_VERSION
        if FEED_NAME not in registry.available(registry.DATA_FEED):
            registry.register(registry.DATA_FEED, FEED_NAME, factory)
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
            "risk": {"rules": [], "allow_empty_chain": True},
        }
        if analysis is not None:
            cfg["analysis"] = analysis
        return BTFRuntime().load_config(cfg).build()

    def test_overlap_disclosed(self):
        rt = self._build(analysis={"crisis_windows": [
            {"name": "测试窗", "start": "2015-01-01", "end": "2015-01-31"}]})
        assert any("危机区间覆盖" in n and "测试窗" in n
                   for n in rt.assembly_notes), rt.assembly_notes

    def test_no_overlap_not_disclosed(self):
        rt = self._build()          # 内置窗全部晚于 2015-01-09（首窗 2015-06 起）
        assert not any("危机区间覆盖" in n for n in rt.assembly_notes)
