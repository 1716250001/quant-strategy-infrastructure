# -*- coding: utf-8 -*-
"""L2 集成：**P18（19 号 §165）**dividend 同日重复公告去重（键去 `end_date`）。

- **重复公告合并**：业务参数（pay/record/送转/cash）全同、仅 `end_date` /
  `ann_date` 不同的多行 ⇒ 只留首行（000958.SZ@20211115 2→1；
  600938.SH@20220715 3 行→1）。
- **真实多笔必须保留**：金额/送转不同 ⇒ 仍为多条（600188.SH@20230717
  3.07+转0.5 与 1.23；300760.SZ@20250529 0.56 与 1.41）。
- **全窗扫描**：2016-2026 同签名重复组 = 0（修复前 32 组）。
- 披露：`feed.dividend_dedup_note` 记合并条数（无合并 ⇒ 空）。
"""
from __future__ import annotations

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.feed import TushareParquetFeed
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l2]

requires_mainlib = pytest.mark.skipif(
    not (MARKET_DATA_DIR / "dividend").is_dir(),
    reason=f"主库不可用: {MARKET_DATA_DIR}")

START = "20160630"
END = "20260918"


@pytest.fixture(scope="module")
def acts():
    feed = TushareParquetFeed(MARKET_DATA_DIR)
    got = feed.corporate_actions(TradingDate.from_ymd(START),
                                 TradingDate.from_ymd(END))
    return feed, got


def _on(acts, sym: str, day: str):
    return [a for a in acts
            if a.symbol == sym and a.ex_date.to_ymd() == day
            and a.rights_ratio == 0.0 and a.consolidation_ratio == 1.0]


@requires_mainlib
class TestDuplicateMerged:
    def test_two_rows_merge_to_one(self, acts):
        """000958.SZ@20211115：同参数两条（end_date 20201231/20211215）→ 1。"""
        _feed, got = acts
        rows = _on(got, "000958.SZ", "20211115")
        assert len(rows) == 1, rows
        assert rows[0].cash_div_per_share == pytest.approx(0.06)

    def test_three_rows_merge_to_one(self, acts):
        """600938.SH@20220715：三行（多年度/多公告）→ 1。"""
        _feed, got = acts
        rows = _on(got, "600938.SH", "20220715")
        assert len(rows) == 1, rows
        assert rows[0].cash_div_per_share == pytest.approx(1.00699, rel=1e-6)

    def test_no_duplicate_signature_in_window(self, acts):
        """全窗：同签名（含业务参数）重复组必须为 0（修复前 32 组）。"""
        _feed, got = acts
        sig: set[tuple] = set()
        dups = []
        for a in got:
            if a.rights_ratio != 0.0 or a.consolidation_ratio != 1.0:
                continue
            k = (a.symbol, a.ex_date.to_ymd(),
                 a.pay_date.to_ymd() if a.pay_date else None,
                 a.record_date.to_ymd() if a.record_date else None,
                 round(a.cash_div_per_share, 8),
                 round(a.stk_div_per_share, 8))
            if k in sig:
                dups.append(k)
            sig.add(k)
        assert not dups, dups[:5]

    def test_notes_record_merged_count(self, acts):
        feed, _got = acts
        note = feed.dividend_dedup_note
        assert "合并" in note and "P18" in note, note


@requires_mainlib
class TestRealMultiPayKept:
    def test_same_day_two_plans_kept(self, acts):
        """600188.SH@20230717：3.07+转0.5（2022 年度）与 1.23（另一笔）都在。

        ⚠ 派单 G#6 写「2 条 → 1 条」；实库两行业务参数**不同**（金额与送转
        均不同）⇒ 按 C#2 属"真实多笔"，合并会少派 1.23 现金并丢掉 0.5 转增。
        实测留 2 条（回执已披露该派单与数据不一致）。
        """
        _feed, got = acts
        rows = _on(got, "600188.SH", "20230717")
        assert len(rows) == 2, rows
        cash = sorted(a.cash_div_per_share for a in rows)
        stk = sorted(a.stk_div_per_share for a in rows)
        assert cash == [pytest.approx(1.23), pytest.approx(3.07)]
        assert stk == [pytest.approx(0.0), pytest.approx(0.5)]

    def test_same_day_two_cash_plans_kept(self, acts):
        """300760.SZ@20250529：0.56 与 1.41 两笔现金 ⇒ 必须保留 2 条。"""
        _feed, got = acts
        rows = _on(got, "300760.SZ", "20250529")
        assert len(rows) == 2, rows
        assert sorted(a.cash_div_per_share for a in rows) == [
            pytest.approx(0.56), pytest.approx(1.41)]


@requires_mainlib
def test_note_empty_when_no_merge():
    """披露不得恒现：区间内无重复公告 ⇒ note 为空串（取无数据的未来区间）。"""
    feed = TushareParquetFeed(MARKET_DATA_DIR)
    feed.corporate_actions(TradingDate.from_ymd("20300101"),
                           TradingDate.from_ymd("20300105"),
                           symbols=["000001.SZ"])
    assert feed.dividend_dedup_note == ""
