# -*- coding: utf-8 -*-
"""L2 集成：ETF 复权走 `fund_adj`（**RC-04，2026-10-05**；真实主库）。

**锚点（本机实测）**：510230.SH（金融 ETF）2020 年发生份额折算 ——
    - 未复权 `fund_daily` 最大日收益 **+78.8%**（20200817，假暴涨）；
    - 经 `fund_adj` 的 hfq 后最大日收益 **10.0%**（折算跳变被抹平）。

**可证伪**：若路由错误（ETF 读 `adj_factor` → 无行 → 因子恒 1.0），复权序列
与未复权完全相同 ⇒ `hfq_max ≈ 0.788` ⇒ 本用例**必红**。

**股票路径**：RC-04 只**新增** ETF 分支，不得动股票口径 —— 本文件另以
「`adj_factor` 表逐位取值一致」守住该边界（完整 hfq 恒等式由
`tests/integration/test_action_equivalence.py` 守护）。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.adjust import TushareAdjustService
from btf.data.feed import TushareParquetFeed
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l2]

ROOT = Path(MARKET_DATA_DIR)
D = TradingDate.from_ymd
ETF_SPLIT = "510230.SH"          # 2020 年份额折算（因子 1.2217 → 6.0059）
STOCK = "600519.SH"


def _skip_if_no_data():
    if not (ROOT / "fund_adj").is_dir():
        pytest.skip("主库 fund_adj 不可用")


@pytest.fixture(scope="module")
def feed():
    _skip_if_no_data()
    return TushareParquetFeed(ROOT)


@pytest.fixture(scope="module")
def adjust():
    _skip_if_no_data()
    return TushareAdjustService(ROOT)


def _max_abs_ret(closes: list[float]) -> float:
    return max(abs(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes)))


class TestEtfShareSplitErased:
    def test_split_jump_erased_by_fund_adj(self, feed, adjust):
        bars = list(feed.bars_of(ETF_SPLIT, D("20200101"), D("20201231"),
                                 daily_table="fund_daily"))
        assert len(bars) > 200, "窗口须覆盖 2020 全年（实测 242 交易日）"
        hfq = adjust.hfq(bars)
        raw_max = _max_abs_ret([b.close for b in bars])
        hfq_max = _max_abs_ret([b.close for b in hfq])
        assert raw_max > 0.50, f"锚点前置：未复权应含折算假跳变（实测 {raw_max:.3f}）"
        assert hfq_max < 0.15, f"折算跳变未被抹平：hfq 最大日收益 {hfq_max:.3f}"
        assert ETF_SPLIT not in adjust.missing_factor_symbols

    def test_factor_not_unit(self, adjust):
        """ETF 因子确实来自 `fund_adj`（非 1.0 回落）。"""
        assert adjust.adj_factor(ETF_SPLIT, D("20201231")) > 1.0


class TestStockPathUnchanged:
    """股票口径**逐位一致**：`adj_factor` 表取值不变（RC-04 只增 ETF 分支）。"""

    def test_stock_factor_equals_table_value(self, adjust):
        from btf.data.core import YearTableStore

        want = adjust.adj_factor(STOCK, D("20200102"))
        store = YearTableStore(ROOT, max_entries=4)
        t = store.symbol_range("adj_factor", STOCK, "20200102", "20200102",
                               ["adj_factor"])
        assert t.num_rows == 1
        assert want == pytest.approx(t.column("adj_factor").to_pylist()[0])


class TestMissingFactorWarnsAndDiscloses:
    """负向对照：**缺覆盖不得静默**（原实现只计数不告警）。

    用空 root 确定性构造"表不可用/无行"场景（不依赖某代码在库中是否恰好缺行）。
    """

    def test_missing_etf_warns_and_discloses(self, tmp_path, caplog):
        svc = TushareAdjustService(tmp_path)         # 空目录 root
        with caplog.at_level(logging.WARNING, logger="btf.data.adjust"):
            factor = svc.adj_factor("510300.SH", D("20201231"))
        assert factor == 1.0                         # 回落值（口径不变）
        assert "510300.SH" in svc.missing_factor_symbols
        assert any("复权因子缺失" in r.getMessage() for r in caplog.records), \
            "缺覆盖必须 warn（不得静默回落）"
        notes = svc.degraded_notes()
        assert notes and "510300.SH" in notes[0] and "fund_adj" in notes[0]

    def test_warning_once_per_symbol(self, tmp_path, caplog):
        """同一标的只告警一次（缓存后不再进 `_load`）——防日志刷屏。"""
        svc = TushareAdjustService(tmp_path)
        with caplog.at_level(logging.WARNING, logger="btf.data.adjust"):
            for _ in range(3):
                svc.adj_factor("510300.SH", D("20201231"))
        hits = [r for r in caplog.records if "复权因子缺失" in r.getMessage()]
        assert len(hits) == 1
