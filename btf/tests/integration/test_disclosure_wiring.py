# -*- coding: utf-8 -*-
"""L4 集成：**披露闭环**与落盘守卫（19 号 §22.2 P1-NEW-5 / §22.3 P2-NEW-3·4 / §24.3 P0-NEW-7）。

覆盖三处「通道未闭环」缺陷：

1. **P1-NEW-5**：`runtime.assembly_notes`（宇宙口径）原**零消费者**——只落
   日志、不进产物 → 跨区间/跨实验不可比。现经 manifest 落盘 → 报告披露区。
2. **P2-NEW-3**：`universe_span` 回退口径（起止快照并集）会漏「期间上市且期间
   退市」标的 → 必须**显式披露**；`MemoryFeed` 补精确 `universe_span`。
3. **P2-NEW-4**：`MixedDailyFeed.universe_span` 委托须 `getattr` 兜底
   （delegate 未实现该可选方法时不得 `AttributeError`）。
4. **P0-NEW-7 第二层**：`save_metrics` 数值守卫——非数值混入时**指名报错**，
   而不是在跑完之后抛 `could not convert string to float`。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from btf import registry
from btf.config.paths import MARKET_DATA_DIR
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
from btf.experiment.manifest import RunManifest
from btf.experiment.store import LocalRunStore

pytestmark = [pytest.mark.l4]

FEED_NAME = "disclosure_wiring_feed"
D = TradingDate.from_ymd


@pytest.fixture()
def _feed_registered():
    from tests.fixtures.scenarios import t_plus1_scenario

    feed = t_plus1_scenario().feed

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, _factory)
    return feed


class TestSaveMetricsGuard:
    """P0-NEW-7 第二层：数值守卫（快速失败 + 可操作报错）。"""

    def test_non_numeric_metric_raises_with_key_name(self, tmp_path):
        store = LocalRunStore(tmp_path)
        cfg = {"schema_version": "backtest.v1"}
        manifest = store.create_run(cfg)
        with pytest.raises(ValueError, match=r"benchmark_symbol|只允许数值"):
            store.save_metrics(manifest.run_id,
                               {"total_return": 0.1,
                                "benchmark_symbol": "000300.SH"})

    def test_numeric_metrics_ok_and_nan_to_null(self, tmp_path):
        store = LocalRunStore(tmp_path)
        manifest = store.create_run({"schema_version": "backtest.v1"})
        store.save_metrics(manifest.run_id,
                           {"total_return": 0.1, "sharpe_ratio": float("nan")})
        raw = json.loads((store.run_dir(manifest.run_id) / "metrics.json")
                         .read_text(encoding="utf-8"))["metrics"]
        assert raw["total_return"] == 0.1
        assert raw["sharpe_ratio"] is None          # NaN → null（严格 JSON）


class TestManifestAssemblyNotes:
    """P1-NEW-5：装配期口径披露须随产物落盘（不再只落日志）。"""

    def test_roundtrip(self):
        manifest = RunManifest.skeleton(
            {"schema_version": "backtest.v1"},
            assembly_notes=["宇宙期间并集：5690 标的（跨期新增 623 只）"])
        again = RunManifest.from_dict(manifest.to_dict())
        assert again.assembly_notes == manifest.assembly_notes

    def test_backward_compatible_without_key(self):
        """旧产物无 `assembly_notes` 键 → 读取器不失败（空元组）。"""
        data = RunManifest.skeleton({}).to_dict()
        data.pop("assembly_notes")
        assert RunManifest.from_dict(data).assembly_notes == ()


class TestUniverseSpanFallback:
    """P2-NEW-3/4：MemoryFeed 精确并集 + 委托 getattr 兜底。"""

    def test_memory_feed_span_is_window_union(self):
        from btf.data.memory import MemoryFeed
        from btf.domain.types import AssetClass, Instrument

        old = Instrument(symbol="000001.SZ", asset_class=AssetClass.STOCK,
                         lot_size=100, list_date=D("20100101"),
                         delist_date=D("20170101"))
        new = Instrument(symbol="688001.SH", asset_class=AssetClass.STOCK,
                         lot_size=100, list_date=D("20190722"))
        feed = MemoryFeed([], {}, {"000001.SZ": old, "688001.SH": new})

        at_start = {i.symbol for i in feed.universe(D("20160104"))}
        span = {i.symbol for i in feed.universe_span(D("20160104"),
                                                     D("20250923"))}
        assert at_start == {"000001.SZ"}          # 新标的起点未上市
        assert span == {"000001.SZ", "688001.SH"}  # 期间并集两者都在

    def test_mixed_feed_delegate_without_span(self):
        """delegate 无 `universe_span` → 起止快照并集（不抛 AttributeError）。"""
        from btf.data.feed import MixedDailyFeed
        from btf.domain.types import AssetClass, Instrument

        class _LegacyFeed:
            contract_version = CONTRACT_VERSION

            def universe(self, date):
                inst = Instrument(symbol="000001.SZ",
                                  asset_class=AssetClass.STOCK, lot_size=100)
                return [inst]

        feed = MixedDailyFeed(Path("."))
        feed._delegate = _LegacyFeed()            # 注入"旧版"委托
        span = feed.universe_span(D("20160104"), D("20161231"))
        assert [i.symbol for i in span] == ["000001.SZ"]


requires_index = pytest.mark.skipif(
    not (Path(MARKET_DATA_DIR) / "index_daily").is_dir(),
    reason=f"主库不可用: {MARKET_DATA_DIR}")


@requires_index
class TestSharedBenchmarkStore:
    """Z-3（19 号 §26.3）：基准取数**双重 IO** 消除——两处共用同一 store。"""

    def test_guard_and_metrics_share_store(self, _feed_registered):
        from btf.runtime import BTFRuntime

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
            "risk": {"rules": [], "allow_empty_chain": True},   # X-7 逃生声明
            "report": {"benchmark": "000300.SH"},
        }
        rt = BTFRuntime().load_config(cfg).build()      # 装配期守卫：装载 index_daily
        store = rt._data_store
        assert store is not None, "runtime 未持共享表级缓存（Z-3 回归）"
        loads_after_guard = store.stats()["loads"]
        assert loads_after_guard > 0, "装配期守卫未走共享 store？"

        # run 期取数（同批年份，同标的）→ 必须**零新装载**（原实现两个私有
        # store → 同一批年份读两遍）。快照取 5 日（相对指标样本方差需 n≥3）
        snapshots = [{"date": d, "total_value": v} for d, v in (
            ("20150105", 1_000_000.0), ("20150106", 1_010_000.0),
            ("20150107", 1_005_000.0), ("20150108", 1_020_000.0),
            ("20150109", 1_015_000.0))]
        metrics = rt._relative_metrics_for("000300.SH", snapshots, analysis={})
        assert metrics, "相对指标为空"
        assert store.stats()["loads"] == loads_after_guard, (
            f"run 期重复装载 index_daily（双重 IO 回归）："
            f"{loads_after_guard} → {store.stats()['loads']}")

    def test_too_short_series_raises_benchmark_error(self):
        """边界：2 日序列 fail-closed 抛 `BenchmarkError`（而非 ZeroDivisionError）。

        样本方差/协方差/跟踪误差均除以 (n−1) → 原守卫 `< 2` 允许 n=2 →
        除零崩溃（Z-2 补测时实测踩到）。
        """
        from btf.analytics.benchmark import BenchmarkError, relative_metrics

        with pytest.raises(BenchmarkError, match=r"序列过短|n−1"):
            relative_metrics([1.0, 1.01], [1.0, 1.02])


class TestAssemblyNotesReachReport:
    """P1-NEW-5 端到端：装配笔记 → manifest → 报告披露区。"""

    def test_notes_persisted_and_rendered(self, _feed_registered, tmp_path):
        from btf.runtime import BTFRuntime

        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "000001.SZ", "weight": 0.9},
                "universe": {"source": "all"},        # 触发期间并集笔记
                "period": {"start": "2015-01-05", "end": "2015-01-09"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": FEED_NAME},
            "risk": {"rules": [], "allow_empty_chain": True},   # X-7 逃生声明
        }
        store = LocalRunStore(tmp_path)
        rt = BTFRuntime().load_config(cfg).build()
        assert rt.assembly_notes, "source=all 应产生宇宙口径笔记"
        result = rt.run(store=store)
        run_id = result.run_id

        manifest = json.loads((store.run_dir(run_id) / "manifest.json")
                              .read_text(encoding="utf-8"))
        assert manifest["assembly_notes"], "口径笔记未落盘（P1-NEW-5 回归）"

        # 报告渲染：口径与装配披露区含该笔记（`build()` 即 HTML 字符串）
        from btf.viz.report import ReportBuilder

        bundle = store.load(run_id)
        report = ReportBuilder(bundle)
        html = report.build() if hasattr(report, "build") else report.build_html()
        assert "口径与装配披露" in html
        assert "source=all 宇宙" in html, "宇宙口径未进报告（P1-NEW-5 回归）"
