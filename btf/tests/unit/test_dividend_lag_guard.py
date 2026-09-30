# -*- coding: utf-8 -*-
"""IO-3/P1-8 守卫：dividend「方案年度 → ex_date」滞后窗口 + 超集等价性。

背景（19 号附录 D.3 / 20 号 §8.2）：`dividend` 年文件按**方案所属年度**归档，
ex_date 可晚于文件年——按 ex_date 年裁剪会**静默漏行动**（PoC-3 实测
600276 漏 20080411 一次 10 送 2 派 0.7 → G9 恒等式偏差 17%）。
今按「ex_date 年 − `_DIV_MAX_LAG_YEARS`」读超集文件 + 行内区间过滤。

本文件是该区间化的**两条防线**（铁律新 15：防线必须可被证伪）：
    ① `test_lag_within_corridor`——全表重测滞后分布，> 窗口即红
       （数据侧引入更长滞后 ⇒ 现在就会静默漏行，必须人工复核窗口）；
    ② `test_padded_read_equals_full_read`——**超集等价性**：区间化读出的行动
       与"全 37 年文件读"逐条一致（这是"没有漏行"的直接证据）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import _DIV_MAX_LAG_YEARS, TushareParquetFeed
from btf.domain.types import TradingDate

ROOT = Path(MARKET_DATA_DIR)
requires_mainlib = pytest.mark.skipif(
    not (ROOT / "dividend").is_dir(), reason=f"主库不可用: {ROOT}")

START, END = "20160101", "20251231"      # 十年区间（引擎 B2/B3 场景口径）


def _max_lag() -> tuple[int, list[str]]:
    """全表重测：max(ex_date 年 − 文件年) 与超窗样例。"""
    import pyarrow.parquet as pq

    worst, samples = 0, []
    for path in sorted((ROOT / "dividend").glob("*.parquet")):
        year = int(path.stem)
        t = pq.read_table(path, columns=["ts_code", "ex_date"])
        for code, ex in zip(t.column("ts_code").to_pylist(),
                            t.column("ex_date").to_pylist(), strict=True):
            if not ex:
                continue
            lag = int(ex[:4]) - year
            if lag > worst:
                worst = lag
            if lag > _DIV_MAX_LAG_YEARS:
                samples.append(f"{code} {path.stem}->{ex}")
    return worst, samples


@requires_mainlib
def test_lag_within_corridor():
    """① 滞后守卫：最长滞后 ≤ `_DIV_MAX_LAG_YEARS`（超窗 ⇒ 立即红）。"""
    worst, over = _max_lag()
    print(f"\ndividend 最长滞后 {worst} 年（窗口 {_DIV_MAX_LAG_YEARS} 年）")
    assert not over, (
        f"出现 > {_DIV_MAX_LAG_YEARS} 年的 ex_date 滞后 {over[:3]}——"
        f"回看窗口已不足，区间化读会**静默漏行**，须提高 _DIV_MAX_LAG_YEARS")
    assert worst <= _DIV_MAX_LAG_YEARS


def _reference_actions(start: str, end: str) -> set[tuple]:
    """参照实现：**全 37 年文件**读 + 同口径过滤/去重（逐字对齐 feed 口径）。"""
    import pyarrow.compute as pc
    from btf.data.parquet_reader import read_full

    t = read_full("dividend", ROOT, columns=[
        "ts_code", "ex_date", "pay_date", "record_date", "ann_date",
        "div_proc", "stk_bo_rate", "stk_co_rate", "cash_div", "end_date"])
    t = t.filter(pc.and_(
        pc.and_(pc.equal(t.column("div_proc"), "实施"),
                pc.greater_equal(t.column("ex_date"), start)),
        pc.less_equal(t.column("ex_date"), end)))
    seen: set[tuple] = set()
    for r in t.to_pylist():
        if not r["ex_date"]:
            continue
        seen.add((r["ts_code"], r["ex_date"], r["pay_date"], r["record_date"],
                  float(r["stk_bo_rate"] or 0.0),
                  float(r["stk_co_rate"] or 0.0),
                  float(r["cash_div"] or 0.0), r["end_date"]))
    return seen


@requires_mainlib
def test_padded_read_equals_full_read():
    """② 超集等价性：区间化读 == 全量读（逐条一致，无静默漏行）。"""
    feed = TushareParquetFeed(ROOT)
    got = feed.corporate_actions(TradingDate.from_ymd(START),
                                 TradingDate.from_ymd(END))
    # 规范键：(symbol, ex_date, pay, record, stk 合计, cash)——两侧同口径
    got_keys = {(a.symbol, a.ex_date.to_ymd(),
                 a.pay_date.to_ymd() if a.pay_date else None,
                 a.record_date.to_ymd() if a.record_date else None,
                 round(a.stk_div_per_share, 6),
                 round(a.cash_div_per_share, 6)) for a in got}
    ref = _reference_actions(START, END)
    ref_keys = {(k[0], k[1], k[2], k[3],
                 round(k[4] + k[5], 6), round(k[6], 6)) for k in ref}
    missing = ref_keys - got_keys
    extra = got_keys - ref_keys
    print(f"\n区间化 {len(got_keys)} 条 vs 全量参照 {len(ref_keys)} 条")
    assert not missing, f"区间化**漏行** {len(missing)} 条（样例 {sorted(missing)[:3]}）"
    assert not extra, f"区间化多出 {len(extra)} 条（样例 {sorted(extra)[:3]}）"


@requires_mainlib
def test_corporate_actions_memoized():
    """③ 进程内 memo：同 (root, start, end) 二次调用命中缓存（IO-3 附带收益）。"""
    from btf.data.feed import _CORPORATE_ACTIONS_CACHE

    feed = TushareParquetFeed(ROOT)
    start, end = TradingDate.from_ymd(START), TradingDate.from_ymd(END)
    before = _CORPORATE_ACTIONS_CACHE.stats()["misses"]
    feed.corporate_actions(start, end)
    mid_hits = _CORPORATE_ACTIONS_CACHE.stats()["hits"]
    feed.corporate_actions(start, end)
    after = _CORPORATE_ACTIONS_CACHE.stats()
    assert after["hits"] > mid_hits, "第二次调用未命中 memo"
    assert before >= 0 and after["entries"] <= _CORPORATE_ACTIONS_CACHE.maxsize
