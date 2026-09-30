# -*- coding: utf-8 -*-
"""批 8（CC-1..CC-6）数据不变量校验单测：**每条规则都有能触发它的 fixture**。

铁律新 15（变异性检查）：本文件的每个"必须被检出"用例都**故意破坏**被保护的
不变量——若校验器漏检，用例变红（防线可被证伪）。反向用例（干净 fixture、
新股豁免、盘中停牌豁免）保证校验器**不是触发器**（假阳性会掩盖真问题）。

铁律新 16（禁空洞披露）：`test_checked_rows_never_empty` 断言五条规则的
`checked_rows` 均 > 0（"校验了"必须有计数证据）。
fail-closed：`test_missing_calendar_is_hard_error` 断言缺交易日历时**拒绝运行**
而非静默跳过。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.data.quality import run_checks

DAYS = ["20240102", "20240103", "20240104", "20240105", "20240108",
        "20240109", "20240110", "20240111", "20240112", "20240115"]
SYM = "600000.SH"
SYM2 = "300001.SZ"          # 创业板（20% 上限）


def _write(root: Path, table: str, rows: list[dict], sub: str = "") -> None:
    d = root / (sub or table)
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "2024.parquet")


def _daily_rows(sym: str = SYM, *, close: float = 10.0,
                vol: float = 1000.0) -> list[dict]:
    return [{"ts_code": sym, "trade_date": d, "open": close, "high": close,
             "low": close, "close": close, "pre_close": close,
             "vol": vol, "amount": close * vol} for d in DAYS]


@pytest.fixture()
def master(tmp_path: Path) -> Path:
    """合成主库（干净基线：五条规则全 PASS）。

    口径：SYM 于 `DAYS[4]`（20240105）**全日停牌** → 该日**不得有行情行**
    （实测主库口径）；SYM2 正常全窗。破坏类用例在此之上逐项注入。
    """
    root = tmp_path / "market"
    (root / "metadata").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"exchange": "SSE", "cal_date": d, "is_open": 1,
         "pretrade_date": DAYS[max(0, i - 1)]}
        for i, d in enumerate(DAYS)]), root / "metadata" / "trade_cal.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": SYM, "name": "浦发银行", "list_date": "19991110",
         "delist_date": None},
        {"ts_code": SYM2, "name": "特锐德", "list_date": "20091030",
         "delist_date": None},
    ]), root / "metadata" / "stock_basic.parquet")
    rows = [r for r in _daily_rows()
            if r["trade_date"] != "20240105"] + _daily_rows(SYM2)
    _write(root, "daily", rows)
    _write(root, "stk_limit", [
        {"ts_code": SYM, "trade_date": d, "up_limit": 11.0, "down_limit": 9.0}
        for d in DAYS] + [
        {"ts_code": SYM2, "trade_date": d, "up_limit": 12.0,
         "down_limit": 8.0} for d in DAYS])
    _write(root, "adj_factor", [
        {"ts_code": SYM, "trade_date": d, "adj_factor": 1.0 + i * 0.01}
        for i, d in enumerate(DAYS)] + [
        {"ts_code": SYM2, "trade_date": d, "adj_factor": 2.0}
        for d in DAYS])
    _write(root, "suspend_d", [
        {"ts_code": SYM, "trade_date": "20240105", "suspend_timing": None,
         "suspend_type": "S"}])
    _write(root, "dividend", [
        {"ts_code": SYM, "ex_date": "20240109", "pay_date": "20240109",
         "record_date": "20240108", "ann_date": "20231201",
         "div_proc": "实施", "stk_bo_rate": None, "stk_co_rate": None,
         "cash_div": 0.1, "end_date": "20231231"}])
    return root


def _check(root: Path, **kw):
    return run_checks(root, DAYS[0], DAYS[-1], symbols={SYM, SYM2},
                      st_symbols=set(), **kw)


def _rule(report, rule: str):
    return next(r for r in report.rules if r.rule == rule)


# ── 基线 ──
def test_clean_master_passes(master: Path):
    """干净 fixture：五条规则全 PASS（校验器不是触发器）。"""
    rep = _check(master)
    assert rep.ok, rep.summary_line()
    assert rep.n_findings == 0


def test_checked_rows_never_empty(master: Path):
    """铁律新 16：五条规则 `checked_rows` 均 > 0（不得恒空）。"""
    rep = _check(master)
    for r in rep.rules:
        assert r.checked_rows > 0, f"{r.rule} checked_rows=0（空洞披露）"


def test_missing_calendar_is_hard_error(master: Path):
    """fail-closed：缺 trade_cal → 硬失败（不得静默跳过 CC-3）。"""
    (master / "metadata" / "trade_cal.parquet").unlink()
    with pytest.raises(FileNotFoundError, match="交易日历缺失"):
        _check(master)


# ── CC-1 ──
def test_cc1_detects_bad_ohlc(master: Path):
    rows = _daily_rows()
    rows[2]["high"] = 1.0                      # high < close
    _write(master, "daily", rows + _daily_rows(SYM2))
    rep = _check(master)
    assert not _rule(rep, "CC-1").ok


def test_cc1_detects_negative_volume(master: Path):
    rows = _daily_rows()
    rows[1]["vol"] = -1.0
    _write(master, "daily", rows + _daily_rows(SYM2))
    assert not _rule(_check(master), "CC-1").ok


# ── CC-2 ──
def test_cc2_detects_limit_breach(master: Path):
    rows = _daily_rows()
    rows[3]["pre_close"] = rows[3]["close"] / 2.0      # −50% 单日
    _write(master, "daily", rows + _daily_rows(SYM2))
    assert not _rule(_check(master), "CC-2").ok


def test_cc2_st_branch_is_triggerable(master: Path):
    """批 9 / DD-2（P2-NEW-8）：ST 分支**必须能被触发**（铁律新 15 双向对照）。

    ST 股单日 **+8%**：> ST 上限 5%、< 板块上限 10% —— 是唯一能区分"ST 分支
    是否生效"的信号区间：
      · 传 `st_symbols` → **检出**（分支真在跑）；
      · 不传（旧行为 `None`）→ **不检出**（= 漏检面，正是 P2-NEW-8 的成因）。
    """
    rows = _daily_rows()
    day = DAYS[2]
    rows[2]["pre_close"] = round(rows[2]["close"] / 1.08, 6)   # +8%
    _write(master, "daily", rows + _daily_rows(SYM2))
    # ① 旧行为（不传 ST）→ 漏检（false negative，非假阳性）
    bare = run_checks(master, DAYS[0], DAYS[-1], symbols={SYM, SYM2})
    assert _rule(bare, "CC-2").ok, (
        "不传 ST 时按板块上限（10%）→ 不报；该漏检面即 P2-NEW-8")
    # ② 接线后（传 ST）→ 必须检出
    wired = run_checks(master, DAYS[0], DAYS[-1], symbols={SYM, SYM2},
                       st_symbols={(SYM, day)})
    assert not _rule(wired, "CC-2").ok, "ST 股 +8%（> 5%）必须检出"
    assert _rule(wired, "CC-2").n_findings >= 1


def test_cc2_st_limit_is_board_aware(master: Path):
    """**ST 5% 只适用主板**（接线后实测校正的口径，反向对照）。

    创业板/科创板 ST 股涨跌幅限制**不变**（20%）：若按"ST ⇒ 一律 5%"会把
    非主板 ST 的日常波动全部误报（v77 十年区间实测 4,393 行假阳性，样例
    300089.SZ@20230103）。本用例用「创业板 ST 股 +8%」钉住该口径：不得报。
    """
    rows = _daily_rows(SYM2)                    # 300001.SZ = 创业板
    day = DAYS[2]
    rows[2]["pre_close"] = round(rows[2]["close"] / 1.08, 6)   # +8% (< 20%)
    _write(master, "daily", _daily_rows() + rows)
    wired = run_checks(master, DAYS[0], DAYS[-1], symbols={SYM, SYM2},
                       st_symbols={(SYM2, day)})
    assert _rule(wired, "CC-2").ok, "创业板 ST 不应按 5% 上限判定"
    # 同一标的按**主板**口径会报（证明该断言仍能被触发：切换板块即变红）
    main_rows = _daily_rows()
    main_rows[2]["pre_close"] = round(main_rows[2]["close"] / 1.08, 6)
    _write(master, "daily", main_rows + rows)
    wired2 = run_checks(master, DAYS[0], DAYS[-1], symbols={SYM, SYM2},
                        st_symbols={(SYM, day)})
    assert not _rule(wired2, "CC-2").ok, "主板 ST +8%（> 5%）必须检出"


def test_cc2_exempts_fresh_ipo(master: Path):
    """新股上市 7 个交易日内不适用常规涨跌幅 → **不得**报（豁免生效）。"""
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": SYM, "name": "浦发银行", "list_date": "19991110",
         "delist_date": None},
        {"ts_code": SYM2, "name": "新股", "list_date": DAYS[0],
         "delist_date": None}]), master / "metadata" / "stock_basic.parquet")
    rows = _daily_rows(SYM2)
    rows[1]["pre_close"] = rows[1]["close"] / 3.0      # 新股次日 −66%
    _write(master, "daily", _daily_rows() + rows)
    assert _rule(_check(master), "CC-2").ok


# ── CC-3 ──
def test_cc3_detects_off_calendar(master: Path):
    rows = _daily_rows()
    rows[0]["trade_date"] = "20240106"                # 周六（不在日历）
    _write(master, "daily", rows + _daily_rows(SYM2))
    assert not _rule(_check(master), "CC-3").ok


def test_cc3_detects_row_on_full_day_suspension(master: Path):
    """全日停牌日出现行情行 → 状态/行情错配（该口径下应无行）。"""
    _write(master, "daily", _daily_rows() + _daily_rows(SYM2))   # 停牌日补行
    assert not _rule(_check(master), "CC-3").ok


def test_cc3_tolerates_intraday_suspension(master: Path):
    """盘中停牌（有 timing）**有行情行是正常的** → 不得报（豁免生效）。"""
    _write(master, "suspend_d", [
        {"ts_code": SYM, "trade_date": "20240105",
         "suspend_timing": "09:30-09:40", "suspend_type": "S"}])
    assert _rule(_check(master), "CC-3").ok


# ── CC-4 ──
def test_cc4_detects_nonpositive_factor(master: Path):
    _write(master, "adj_factor", [
        {"ts_code": SYM, "trade_date": DAYS[0], "adj_factor": 0.0},
        {"ts_code": SYM, "trade_date": DAYS[1], "adj_factor": 1.0},
    ])
    assert not _rule(_check(master), "CC-4").ok


def test_cc4_detects_monotonic_break(master: Path):
    """真断链（量级下降）必须检出；末位舍入（<1e-3）不得误报。"""
    _write(master, "adj_factor", [
        {"ts_code": SYM, "trade_date": DAYS[0], "adj_factor": 3.0},
        {"ts_code": SYM, "trade_date": DAYS[1], "adj_factor": 1.5},
        {"ts_code": SYM, "trade_date": DAYS[2], "adj_factor": 1.5 - 0.0002},
    ])
    rep = _check(master)
    r = _rule(rep, "CC-4")
    assert not r.ok
    mono = [f for f in r.findings if "下降" in f.detail]
    assert len(mono) == 1 and mono[0].n_rows == 1, (
        f"单调断链检出数不符（舍入噪声被误报？）："
        f"{[(f.detail, f.n_rows) for f in r.findings]}")


def test_cc4_detects_event_without_factor_row(master: Path):
    """除权事件（20240109）缺因子行 → 复权链缺口。"""
    _write(master, "adj_factor", [
        {"ts_code": SYM, "trade_date": DAYS[0], "adj_factor": 1.0}])
    assert not _rule(_check(master), "CC-4").ok


# ── CC-5 ──
def test_cc5_detects_zero_volume_row(master: Path):
    rows = _daily_rows()
    rows[4]["vol"] = 0.0
    rows[4]["amount"] = 0.0
    _write(master, "daily", rows + _daily_rows(SYM2))
    assert not _rule(_check(master), "CC-5").ok


def test_cc5_detects_close_outside_limit(master: Path):
    rows = _daily_rows()
    rows[6]["close"] = 30.0                    # 远超 up_limit=11
    rows[6]["high"] = 30.0
    rows[6]["low"] = 30.0
    rows[6]["open"] = 30.0
    rows[6]["amount"] = 30.0 * 1000.0
    _write(master, "daily", rows + _daily_rows(SYM2))
    assert not _rule(_check(master), "CC-5").ok


def test_empty_symbols_is_noop(master: Path):
    """空标集：无行可校验（不得因空 value_set 崩溃）。"""
    rep = run_checks(master, DAYS[0], DAYS[-1], symbols=set())
    assert rep.symbols == 0 and rep.rules


def test_copies_do_not_leak(master: Path, tmp_path: Path):
    """独立副本语义（防测试间串扰）：篡改副本不影响原 fixture。"""
    other = tmp_path / "market2"
    shutil.copytree(master, other)
    rows = _daily_rows()
    rows[0]["vol"] = -5.0
    _write(other, "daily", rows + _daily_rows(SYM2))
    assert not _rule(_check(other), "CC-1").ok
    assert _rule(_check(master), "CC-1").ok
