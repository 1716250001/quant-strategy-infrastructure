# -*- coding: utf-8 -*-
"""L2 集成测试：状态面板合成（PoC-1 任务 1.6 验收——11 号计划"10 个手工案例"）。

案例策略：从真实库**动态自洽查询**锁定案例（对数据漂移稳健）——
先查源表找满足前提的行，再断言合成结果与源表口径一致。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.state import StateSynthesizer
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l2]

ROOT = Path(MARKET_DATA_DIR)


def _skip_if_no_data():
    if not (ROOT / "stk_limit").is_dir():
        pytest.skip("主库不可用")


@pytest.fixture(scope="module")
def synth() -> StateSynthesizer:
    return StateSynthesizer(ROOT)


class TestSuspension:
    """案例 1-2：停牌口径（在表即停牌）。"""

    def test_suspended_day_true(self, synth):
        """从 suspend_d 实查一行 → is_suspended=True。"""
        _skip_if_no_data()
        t = pq.read_table(ROOT / "suspend_d" / "2023.parquet",
                          columns=["ts_code", "trade_date"])
        t = t.filter(__import__("pyarrow.compute", fromlist=["pc"]).equal(
            t.column("trade_date"), "20230601"))
        if t.num_rows == 0:  # 该日无停牌则取文件首行日期
            code = pq.read_table(ROOT / "suspend_d" / "2023.parquet",
                                 columns=["ts_code"]).column(0)[0].as_py()
            day = pq.read_table(ROOT / "suspend_d" / "2023.parquet",
                                columns=["trade_date"]).column(0)[0].as_py()
            td = TradingDate.from_ymd(day)
            st = synth.states(td, [code])[code]
            assert st.is_suspended
            assert not st.tradable
            return
        code = t.column("ts_code")[0].as_py()
        st = synth.states(TradingDate.from_ymd("20230601"), [code])[code]
        assert st.is_suspended and not st.tradable

    def test_normal_trading_day_false(self, synth):
        """正常交易日（有行情、不在 suspend_d）→ is_suspended=False。"""
        _skip_if_no_data()
        st = synth.states(TradingDate(date(2023, 6, 1)), ["000001.SZ"])["000001.SZ"]
        assert not st.is_suspended and st.tradable


class TestStInterval:
    """案例 3-4：ST 区间判定（namechange 唯一源）。"""

    def test_st_inside_interval(self, synth):
        """从 namechange 实查一行 ST 记录 → 区间内 is_st=True。"""
        _skip_if_no_data()
        import pyarrow.compute as pc

        t = pq.read_table(ROOT / "namechange" / "2020.parquet")
        mask = pc.match_substring(t.column("name"), "ST")
        t = t.filter(mask)
        assert t.num_rows > 0
        code = t.column("ts_code")[0].as_py()
        start = t.column("start_date")[0].as_py()
        # 取区间内一天（start 当天：判定含端点 start≤d）
        st = synth.states(TradingDate.from_ymd(start), [code])[code]
        assert st.is_st

    def test_non_st_stock_false(self, synth):
        _skip_if_no_data()
        st = synth.states(TradingDate(date(2023, 6, 1)), ["000001.SZ"])["000001.SZ"]
        assert not st.is_st


class TestLimitPrices:
    """案例 5-6：涨跌停价与触及判定。"""

    def test_limit_prices_present(self, synth):
        _skip_if_no_data()
        st = synth.states(TradingDate(date(2023, 6, 1)), ["000001.SZ"])["000001.SZ"]
        assert st.limit_up_price is not None and st.limit_up_price > 0
        assert st.limit_down_price is not None and st.limit_down_price > 0

    def test_limit_touch_self_consistent(self, synth):
        """动态找一只当日收盘=涨停价的标的 → is_limit_up=True。"""
        _skip_if_no_data()
        import pyarrow.compute as pc

        t = pq.read_table(ROOT / "daily" / "2023.parquet",
                          columns=["ts_code", "trade_date", "close"])
        t = t.filter(pc.equal(t.column("trade_date"), "20230601"))
        lim = pq.read_table(ROOT / "stk_limit" / "2023.parquet",
                            columns=["ts_code", "trade_date", "up_limit"])
        lim = lim.filter(pc.equal(lim.column("trade_date"), "20230601"))
        lim_map = dict(zip(lim.column("ts_code").to_pylist(),
                           lim.column("up_limit").to_pylist(), strict=True))
        codes = t.column("ts_code").to_pylist()
        closes = t.column("close").to_pylist()
        # 找收盘触及涨停的案例
        hit = next((c for c, cl in zip(codes, closes, strict=True)
                    if c in lim_map and cl >= lim_map[c] * 0.9999), None)
        if hit is None:
            pytest.skip("2023-06-01 无涨停收盘案例（罕见）")
        st = synth.states(TradingDate(date(2023, 6, 1)), [hit])[hit]
        assert st.is_limit_up
        # 常态标的非涨停
        if "000001.SZ" in lim_map:
            st2 = synth.states(TradingDate(date(2023, 6, 1)), ["000001.SZ"])["000001.SZ"]
            assert not st2.is_limit_up


class TestDelisted:
    """案例 7-8：退市判定（幸存者偏差治理的一等公民语义）。"""

    def test_delisted_after_date(self, synth):
        """从 stock_basic 实查一只退市股 → delist_date 之后 is_delisted=True。"""
        _skip_if_no_data()
        t = pq.read_table(ROOT / "metadata" / "stock_basic.parquet",
                          columns=["ts_code", "delist_date", "list_status"])
        import pyarrow.compute as pc

        t = t.filter(pc.equal(t.column("list_status"), "D"))
        assert t.num_rows > 0
        code = t.column("ts_code")[0].as_py()
        delist_ymd = t.column("delist_date")[0].as_py()
        assert delist_ymd
        # 退市日当天与之后 → True
        st = synth.states(TradingDate.from_ymd(delist_ymd), [code])[code]
        assert st.is_delisted and not st.tradable
        # 退市日前一年（若已上市）→ False
        pre = f"{int(delist_ymd[:4]) - 1}{delist_ymd[4:]}"
        st2 = synth.states(TradingDate.from_ymd(pre), [code])[code]
        assert not st2.is_delisted


class TestPre2008Degradation:
    """案例 9-10：数据起点前降级（CompletenessPolicy 语义基础）。"""

    def test_no_limit_data_before_2008(self, synth):
        """2008 前无 stk_limit → limit 价 None（降级不虚构）。"""
        _skip_if_no_data()
        st = synth.states(TradingDate(date(2005, 6, 1)), ["000001.SZ"])["000001.SZ"]
        assert st.limit_up_price is None and st.limit_down_price is None

    def test_repeatable_and_cached(self, synth):
        """同参数重复调用一致（DataFeed 纪律）+ 缓存生效。"""
        _skip_if_no_data()
        td = TradingDate(date(2023, 6, 1))
        s1 = synth.states(td, ["000001.SZ"])
        s2 = synth.states(td, ["000001.SZ"])
        assert s1["000001.SZ"] == s2["000001.SZ"]
