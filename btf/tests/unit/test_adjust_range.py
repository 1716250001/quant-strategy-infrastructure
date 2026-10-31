# -*- coding: utf-8 -*-
"""L1：第 3/4 批（19 号 §199）——qfq 单因子调用 + 装载区间收窄。

T-1 五字段同因子：结果与逐字段调用**逐属性相等**（且运算顺序 `(a × f) / base`）
T-2 区间装载与全量装载：区间内取值完全相同
T-3 越界 ⇒ 回退全量 + 只告警一次（"静默错误"的唯一防线）
T-4 两个区间先后装载同一标的 ⇒ 互不覆盖（共享缓存键含区间）
"""
from __future__ import annotations

import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.data.adjust import TushareAdjustService
from btf.domain.market import Bar
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l1]

SYM = "600000.SH"


def _bar(ymd: str, px: float) -> Bar:
    d = TradingDate.from_ymd(ymd)
    return Bar(symbol=SYM, date=d, open=px, high=px + 0.5, low=px - 0.5,
               close=px + 0.2, volume=1000.0, amount=px * 1000.0,
               pre_close=px - 0.1)


def _write_years(root: Path, years: list[int]) -> None:
    """稀疏年文件：每年一行因子（20050102=2.0 …），覆盖装载区间判断。"""
    (root / "adj_factor").mkdir(parents=True, exist_ok=True)
    for y in years:
        pq.write_table(pa.table({
            "ts_code": pa.array([SYM], type=pa.large_string()),
            "trade_date": pa.array([f"{y}0102"], type=pa.large_string()),
            "adj_factor": pa.array([float(y - 1990)], type=pa.float64()),
        }), root / "adj_factor" / f"{y}.parquet")


# ── T-1 ─────────────────────────────────────────────────────
def test_t1_qfq_single_factor_lookup_and_exact_order():
    """T-1：每 bar 只调一次 `_af`；五字段结果与手算 `(a × f) / base` 逐属性相等。"""
    calls = {"n": 0}
    svc = TushareAdjustService()
    svc._cache[SYM] = (["20160104", "20160105"], [1.0, 2.0])
    orig = svc.adj_factor

    def counting(symbol, date):
        calls["n"] += 1
        return orig(symbol, date)

    svc.adj_factor = counting                    # type: ignore[method-assign]
    bars = [_bar("20160104", 10.0), _bar("20160105", 11.0)]
    ref = TradingDate.from_ymd("20160104")
    out = svc.qfq(bars, ref)
    base = 1.0
    for src, got in zip(bars, out, strict=True):
        f = 1.0 if src.date.to_ymd() == "20160104" else 2.0
        assert got.open == src.open * f / base
        assert got.high == src.high * f / base
        assert got.low == src.low * f / base
        assert got.close == src.close * f / base
        assert got.pre_close == src.pre_close * f / base
    # 2 bar × 1 次 + base 1 次 = 3（旧实现为 2 × 5 + 1 = 11）
    assert calls["n"] == len(bars) + 1, f"adj_factor 调用 {calls['n']} 次"


# ── T-2 ─────────────────────────────────────────────────────
def test_t2_range_load_equals_full_load(tmp_path):
    """T-2：区间装载（12 年）与全量装载在同一日期上取值相同。"""
    _write_years(tmp_path, [1995, 2005, 2015, 2016, 2017])
    svc = TushareAdjustService(root=tmp_path)
    d = TradingDate.from_ymd("20160106")
    full = svc._load(SYM)
    part = svc._load(SYM, "20050101", "20170101")
    assert len(part[0]) < len(full[0]), "区间装载未收窄"
    svc._prime(SYM, "20050101", "20170101")
    assert svc.adj_factor(SYM, d) == full[1][full[0].index("20160102")]


# ── T-3 ─────────────────────────────────────────────────────
def test_t3_out_of_range_falls_back_and_warns_once(tmp_path, caplog):
    """T-3：查早于装载起点的日期 ⇒ 回退全量、取值与全量一致、告警一次。"""
    _write_years(tmp_path, [1995, 2005, 2015, 2017])
    svc = TushareAdjustService(root=tmp_path)
    svc._prime(SYM, "20170101", "20180101")     # 区间内首行 = 20170102
    caplog.set_level(logging.WARNING)
    old = TradingDate.from_ymd("20000105")       # 早于装载起点
    expect = svc._load(SYM)[1][0]                # 全量表内最早因子
    got = svc.adj_factor(SYM, old)
    assert got == expect == 5.0
    assert svc.adj_factor(SYM, old) == expect    # 第二次不再触发回退
    msgs = [r.message for r in caplog.records if "复权因子越界" in r.message]
    assert len(msgs) == 1, f"告警 {len(msgs)} 次（期望 1）"


# ── T-4 ─────────────────────────────────────────────────────
def test_t4_two_ranges_do_not_overwrite(tmp_path):
    """T-4：同标的两区间先后装载互不覆盖（`_SHARED_ROWS` 键含区间）。"""
    _write_years(tmp_path, [1995, 2005, 2015, 2017])
    svc = TushareAdjustService(root=tmp_path)
    a = svc._load(SYM, "20150101", "20160101")
    b = svc._load(SYM, "20170101", "20180101")
    assert a[0] == ["20150102"] and a[1] == [25.0]
    assert b[0] == ["20170102"] and b[1] == [27.0]
    assert svc._load(SYM, "20150101", "20160101") == a    # 再取仍是各自切片
    keys = {k for k in TushareAdjustService._SHARED_ROWS
            if k.startswith(str(tmp_path))}
    assert len(keys) == 2, f"共享缓存键 {keys}"
