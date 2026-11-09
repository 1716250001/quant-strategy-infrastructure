# -*- coding: utf-8 -*-
"""EE-2（OBS-3）口径标定单测：**六条子口径双向可证伪**（19 号 §52.3 / 铁律新 15）。

覆盖（每条都有"必须不报"与"必须报"两侧）：
    ① 退市整理期：**首日不设涨跌幅** → 不报；窗口内非首日超板块上限 → 报
    ② ST 时点化：主板 ST 5%（**2026-07-06 前**，+8% 报）；其后 10%（+8% 不报）
    ③ 价格档位容差：低价股 +5.56% 不报（1 档/pre_close）；+8% 报
    ④ 前一日无行情 → 不判（不可比）
    ⑤ `*.BJ` 段 → 口径外豁免（计数披露）
    ⑥ 新股锚点 fallback：`list_date` 缺失以首个行情日为锚

构造约定：目标行之前插一行**平静基准行**（`20231229`），使目标行的"前一交易日
有行情"⇒ 真正进入涨跌幅判定（否则会被"前一日无行情 ⇒ 不判"豁免——该口径本身
由 `TestMissingPrevRow` 单独覆盖）。
"""
from __future__ import annotations

import pyarrow as pa
from btf.data import quality

CAL = ["20231229", "20240102", "20240103", "20240104", "20240105",
       "20240108", "20240109", "20240110", "20240111", "20240112",
       "20240115", "20240116", "20240117", "20240118", "20240119",
       "20240122", "20240123", "20240124", "20240125", "20240126",
       "20260703", "20260706", "20260707"]


def _row(code: str, day: str, pre: float, close: float) -> dict:
    return {"ts_code": code, "trade_date": day, "pre_close": pre,
            "close": close}


def _seq(code: str, day: str, pre: float, close: float) -> list[dict]:
    """基准行（20231229，平静）+ 目标行。"""
    return [_row(code, "20231229", 10.0, 10.0), _row(code, day, pre, close)]


def _check(rows: list[dict], *, st: tuple = (), win: tuple = (),
           first: tuple = (), list_dates: dict | None = None,
           fund_meta: dict | None = None, table: str = "daily"):
    """默认把所有代码视为**老股**（`list_date` 早于日历起点）——IPO 分支不介入。

    `list_dates={}`（显式传空）用于测"`list_date` 缺失 → 首个行情日锚点"。
    """
    if list_dates is None:
        list_dates = {r["ts_code"]: "20000101" for r in rows}
    t = pa.Table.from_pylist(rows)
    return quality._check_cc2(t, table, set(st), list_dates, set(CAL),
                              set(), set(win), set(first), fund_meta)


def _n(result) -> int:
    rule, _counts = result
    return rule.n_findings


class TestDelistingPeriod:
    """① 退市整理期：首日不设涨跌幅；其后按板块上限。"""

    def test_first_day_exempt(self):
        result = _check(_seq("600001.SH", "20240102", 1.0, 0.2),   # −80%
                        win=(("600001.SH", "20240102"),),
                        first=(("600001.SH", "20240102"),))
        assert result[1]["exempt_delisting_first_rows"] == 1
        assert _n(result) == 0, "整理期首日应豁免（不设涨跌幅）"

    def test_non_first_day_beyond_limit_reported(self):
        result = _check(_seq("600001.SH", "20240102", 10.0, 14.0),  # +40%
                        win=(("600001.SH", "20240102"),),
                        first=(("600001.SH", "20240103"),))
        assert result[1]["delisting_window_rows"] == 1
        assert _n(result) == 1, "整理期非首日超限必须检出"


class TestStTimePoint:
    """② ST 5% 的**时点化**（EE-2）：2026-07-06 起主板 ST 与普通股一致（10%）。"""

    def test_before_20260706_reported(self):
        result = _check(_seq("600001.SH", "20240102", 10.0, 10.8),
                        st=(("600001.SH", "20240102"),))          # +8% > 5%
        assert _n(result) == 1, "2026-07-06 前主板 ST +8% 必须检出"

    def test_from_20260706_not_reported(self):
        rows = [_row("600001.SH", "20260703", 10.0, 10.0),
                _row("600001.SH", "20260706", 10.0, 10.8)]
        result = _check(rows, st=(("600001.SH", "20260706"),))
        assert _n(result) == 0, (
            "2026-07-06 起主板 ST 上限 10%（+8% 不应报）——时点分支未生效")

    def test_after_20260706_still_reports_beyond_10pct(self):
        rows = [_row("600001.SH", "20260703", 10.0, 10.0),
                _row("600001.SH", "20260706", 10.0, 10.0),
                _row("600001.SH", "20260707", 10.0, 12.0)]        # +20%
        result = _check(rows, st=(("600001.SH", "20260707"),))
        assert _n(result) == 1, "ST 放宽至 10% 后 +20% 仍须检出"

    def test_non_main_board_st_keeps_board_limit(self):
        """创业板 ST 维持 20%：+8% 不报（与主板 5% 形成对照）。"""
        result = _check(_seq("300001.SZ", "20240102", 10.0, 10.8),
                        st=(("300001.SZ", "20240102"),))
        assert _n(result) == 0


class TestTickTolerance:
    """③ 价格档位容差（涨停价四舍五入到分）：低价股 1 档可达 0.5%+。

    **可证伪边界（P3-NEW-6 登记；19 号 §54.2.4）**：可达涨幅 = `k·tick/pre_close`
    是**离散网格**，且「不超过 Limit + 1 档」的最大可达点**恰等于该上界** ⇒
    严格落在 `(_PCT_TOL, 1 档)` 之间的样本**在网格上不存在**。故判别样本取
    **恰在上界**（1.80 → 1.90，+5.556%）：**无 1 档容差 ⇒ 必报**
    （5.556% > 5% + 0.5%）；**有 1 档容差 ⇒ 不报**（≤5% + 0.556%）。为消除边界
    处的 1 ULP 敏感性，实现侧比较带 `_PCT_EPS` ⇒ 该分支**可区分**（而非仅方向可证伪）。
    """

    def test_low_price_tick_at_upper_bound_not_reported(self):
        """恰在上界：1.80 元 ST 股 +5.556%（= 5% + 1 档）⇒ **不报**。"""
        result = _check(_seq("600001.SH", "20240102", 1.80, 1.90),
                        st=(("600001.SH", "20240102"),))
        assert result[1]["tick_tolerance_rows"] >= 1
        assert _n(result) == 0, (
            "1.80 元股 +5.556% 恰为「5% + 1 档」上界——应判未越界；"
            "若此处报出 ⇒ 1 档容差缺失或比较侧未留浮点松弛")

    def test_low_price_tick_within_tolerance(self):
        """界内：1.80 元 ST 股 +5.0% ⇒ **不报**（方向性防线，保留）。"""
        result = _check(_seq("600001.SH", "20240102", 1.80, 1.89),
                        st=(("600001.SH", "20240102"),))
        assert result[1]["tick_tolerance_rows"] >= 1
        assert _n(result) == 0, (
            "1.80 元股 +5.0% 在 5%+0.56%（1 档）内——不应误报")

    def test_low_price_beyond_tick_reported(self):
        """1.80 元 ST 股 +8.33% ⇒ 超 1 档容差 ⇒ **必须报**。"""
        result = _check(_seq("600001.SH", "20240102", 1.80, 1.95),
                        st=(("600001.SH", "20240102"),))
        assert _n(result) == 1, "+8.33% 超 1 档容差，必须检出"

    def test_high_price_tolerance_stays_tight(self):
        """高价股容差仍为 0.5%：10.00 元 ST 股 +5.6% 必报（档位口径不放宽）。"""
        result = _check(_seq("600001.SH", "20240102", 10.0, 10.56),
                        st=(("600001.SH", "20240102"),))
        assert _n(result) == 1


class TestFundBoardLimitD1:
    """⑦ 基金 20% 档（D-1；19 号 §255）：**跟踪标的板块 × 时点**。

    双向可证伪：同一只基金在时点**后** +15% 不报、时点**前** +15% 必报；
    混合型 / 无在册记录在时点后仍必报（证明不是"一律 20%"）。
    """

    def _run(self, code: str, base: str, target: str, meta: dict):
        """日历只含相邻两日——保证目标行"前一交易日有行情"（不被口径外豁免）。"""
        rows = [_row(code, base, 10.0, 10.0), _row(code, target, 10.0, 11.5)]
        return quality._check_cc2(
            pa.Table.from_pylist(rows), "fund_daily", set(),
            {code: "19980101"}, {base, target}, set(), set(), set(), meta)

    def test_gem_after_20200824_not_reported(self):
        result = self._run("159915.SZ", "20200820", "20200824",
                           {"159915": ("股票型", "创业板指数P×100%")})
        assert _n(result) == 0, "创业板 ETF 20% 档（2020-08-24 起）+15% 不应报"

    def test_gem_before_20200824_reported(self):
        result = self._run("159915.SZ", "20200820", "20200821",
                           {"159915": ("股票型", "创业板指数P×100%")})
        assert _n(result) == 1, "2020-08-24 前创业板 ETF 仍 10% ⇒ +15% 必报"
        assert result[1]["fund_limit_by_source"]["equity_10"] == 1

    def test_star_after_20190722_not_reported(self):
        result = self._run("588000.SH", "20190719", "20190722",
                           {"588000": ("股票型", "上证科创板50成份指数收益率×100%")})
        assert _n(result) == 0

    def test_star_before_20190722_reported(self):
        result = self._run("588000.SH", "20190718", "20190719",
                           {"588000": ("股票型", "上证科创板50成份指数收益率×100%")})
        assert _n(result) == 1
        assert result[1]["fund_limit_by_source"]["equity_10"] == 1

    def test_mixed_type_not_relaxed(self):
        """混合型即使 benchmark 含创业板也**不**放宽（派单 §3.3 ②）。"""
        result = self._run("506009.SH", "20200820", "20200824",
                           {"506009": ("混合型", "创业板指数收益率×100%")})
        assert _n(result) == 1, "混合型不得按 20% 放宽"
        assert result[1]["fund_limit_by_source"]["non_equity_10"] == 1

    def test_unregistered_reported_and_counted(self):
        result = self._run("159999.SZ", "20200820", "20200824", {})
        assert _n(result) == 1, "无在册记录 ⇒ 10% 兜底，+15% 必报（不得静默放宽）"
        assert result[1]["fund_limit_undetermined_rows"] == 1
        assert result[1]["fund_limit_by_source"]["undetermined_10"] == 1


class TestMissingPrevRow:
    """④ 前一日无行情 ⇒ 涨跌幅不可比 ⇒ 不判。"""

    def test_missing_prev_row_exempt(self):
        rows = [_row("600001.SH", "20231229", 10.0, 10.0),
                _row("600001.SH", "20240102", 10.0, 10.0),
                _row("600001.SH", "20240105", 10.0, 5.0)]        # 20240104 缺行
        result = _check(rows)
        # 首行（20231229，无前日）+ 目标行（20240105，20240104 缺行）各计 1
        assert result[1]["exempt_missing_prev_rows"] >= 1
        assert _n(result) == 0

    def test_adjacent_prev_row_still_judged(self):
        rows = [_row("600001.SH", "20231229", 10.0, 10.0),
                _row("600001.SH", "20240102", 10.0, 10.0),
                _row("600001.SH", "20240103", 10.0, 5.0)]        # 相邻 → 可比
        result = _check(rows)
        assert _n(result) == 1


class TestBjSegmentAndIpoAnchor:
    """⑤ `*.BJ` 段口径外；⑥ 新股锚点 fallback。"""

    def test_bj_segment_exempt_and_counted(self):
        result = _check(_seq("920001.BJ", "20240102", 1.0, 3.0))
        assert result[1]["exempt_neeq_period_rows"] >= 1
        assert _n(result) == 0

    def test_ipo_anchor_fallback_first_day_exempt(self):
        """`list_date` 缺失 → 以首个行情日为锚：窗口内 +100% 不报。"""
        rows = [_row("688999.SH", "20231229", 10.0, 10.0),
                _row("688999.SH", "20240102", 10.0, 20.0),
                _row("688999.SH", "20240103", 20.0, 40.0)]
        result = _check(rows, list_dates={})       # 模拟 list_date 缺失
        assert result[1]["exempt_ipo_anchor_fallback_rows"] >= 2
        assert _n(result) == 0

    def test_ipo_anchor_window_expires(self):
        """锚点后第 9 个交易日起恢复校验（窗口有界，非永久豁免）。"""
        base = [_row("688999.SH", "20231229", 10.0, 10.0)]
        rows = base + [_row("688999.SH", d, 10.0, 10.0)
                       for d in CAL[1:9]]
        rows.append(_row("688999.SH", CAL[9], 10.0, 20.0))       # 第 9 日 +100%
        result = _check(rows, list_dates={})
        assert _n(result) == 1, "窗口外必须恢复校验"
