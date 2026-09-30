# -*- coding: utf-8 -*-
"""L2 集成测试：真实主库 adj_factor/daily 自洽性（PoC-3 任务 3.1 验收）。

依赖真实主库（D:\\全量数据\\market_data）——只读。

选样：库内实施态送转次数最多的 5 只（dividend 全表统计 2026-09-26）：
    600276.SH(18) 000001.SZ(14) 000002.SZ(14) 600811.SH(14) 600867.SH(14)

hfq 连续性断言（18 号计划 3.1 / 11 §21.1 验证点）：
    close(t−1) × af(t−1) ≈ pre_close(t) × af(t)   对全部相邻交易日对
    ——除权日 af 跳变恰好补偿价格除权（后复权=分红再投资口径，价格链
    连续无跳变；G9 事件链≡hfq 等价断言的数学前提）。Tushare 库内两表
    由同一除权参数生成，自洽性高；残差来自价格小数位舍入，容差 0.5%
    + 异常率 <1%（数据质量护栏，非引擎正确性判据）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.adjust import TushareAdjustService
from btf.data.feed import TushareParquetFeed
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l2]

ROOT = Path(MARKET_DATA_DIR)
SYMBOLS = ["600276.SH", "000001.SZ", "000002.SZ", "600811.SH", "600867.SH"]
START = TradingDate.from_ymd("19910101")
END = TradingDate.from_ymd("20261231")
REL_TOL = 0.005          # 0.5%：送转重缩放 + 价格两位小数舍入
MAX_BAD_RATE = 0.01      # 异常率护栏 1%


def _skip_if_no_data():
    if not (ROOT / "adj_factor").is_dir():
        pytest.skip("主库 adj_factor 不可用")


@pytest.fixture(scope="module")
def feed():
    _skip_if_no_data()
    return TushareParquetFeed(ROOT)


@pytest.fixture(scope="module")
def adjust():
    _skip_if_no_data()
    return TushareAdjustService(ROOT)


class TestHfqContinuityReal:
    """5 只多次送转个股全历史 hfq 连续性（真实数据）。"""

    @pytest.mark.parametrize("symbol", SYMBOLS)
    def test_hfq_close_no_jump_at_ex_dates(self, feed, adjust, symbol):
        from itertools import pairwise

        bars = feed.bars_of(symbol, START, END)
        assert len(bars) > 1_000                       # 全历史量级护栏
        bad = 0
        n = 0
        for prev, cur in pairwise(bars):
            af_prev = adjust.adj_factor(symbol, prev.date)
            af_cur = adjust.adj_factor(symbol, cur.date)
            lhs = prev.close * af_prev                 # hfq 收盘(t−1)
            rhs = cur.pre_close * af_cur               # hfq 前收盘(t)
            n += 1
            if abs(rhs - lhs) > REL_TOL * max(abs(lhs), 1e-9):
                bad += 1
        assert bad / n < MAX_BAD_RATE, (
            f"{symbol}: hfq 连续性异常率 {bad}/{n}={bad / n:.4f} 超护栏")

    def test_adjust_factor_monotone_nondecreasing(self, adjust):
        """后复权因子单调不减（除权日跳升、平日恒定——分红再投资
        口径的必要条件；只计存在因子行的交易日）。

        容差 1e-3：Tushare 因子第 5 位小数舍入噪声（600276 全历史实测
        3 处 −0.0002~−0.0004，非真实除权下降）。
        """
        from itertools import pairwise

        import pyarrow.compute as pc
        from btf.data.parquet_reader import read_range

        t = read_range("adj_factor", ROOT, "19910101", "20261231",
                       columns=["ts_code", "trade_date", "adj_factor"])
        t = t.filter(pc.equal(t.column("ts_code"), "600276.SH"))
        t = t.sort_by([("trade_date", "ascending")])
        factors = t.column("adj_factor").to_pylist()
        assert len(factors) > 5_000
        assert all(b >= a - 1e-3 for a, b in pairwise(factors))


class TestFeedCorporateActionsReal:
    """feed.corporate_actions 真实数据合成（任务 3.2 数据侧）。"""

    def test_impl_only_and_sorted(self, feed):
        acts = feed.corporate_actions(TradingDate.from_ymd("19910101"),
                                      TradingDate.from_ymd("20251231"))
        assert len(acts) > 10_000                      # 全市场全历史量级护栏
        # 升序确定性
        keys = [(a.ex_date.to_ymd(), a.symbol) for a in acts]
        assert keys == sorted(keys)
        # 2000 年前老数据含 ex≠pay（两时点路径真实覆盖）；2020+ 主流 pay≡ex
        old = [a for a in acts
               if a.ex_date.to_ymd() < "20000101" and a.pay_date is not None]
        assert old, "老数据 ex≠pay 样本缺席——两时点路径失去真实覆盖"
        modern = [a for a in acts if a.ex_date.to_ymd() >= "20200101"]
        eq_ratio = sum(1 for a in modern
                       if a.pay_date is not None and a.pay_date == a.ex_date)
        assert eq_ratio / len(modern) > 0.5            # 2020+ pay≡ex 主流口径

    def test_pay_date_missing_fallback_present(self, feed):
        """实施态 pay_date 缺失行存在（E5 回退路径有真实依据）。"""
        acts = feed.corporate_actions(TradingDate.from_ymd("19910101"),
                                      TradingDate.from_ymd("20251231"))
        missing = [a for a in acts if a.pay_date is None
                   and a.cash_div_per_share > 0]
        assert missing, "pay_date 缺失实施行缺席——E5 回退失去真实依据"
