# -*- coding: utf-8 -*-
"""REQ-20261126-01 T6：逐标的最新日期体检（分档 / 豁免 / 负向用例）。

判据（00 §13.2.2 裁定）：
  - 非 REIT：n≥90 日频（>3 黄 / >5 红）；10–89 周频（>10 黄 / >20 红）；<10 半年；
  - REIT：n≥97 日频；10–96 周频/REIT；<10 半年；
  - 半年披露仅在 1/7 月 +30 天后、≥90 自然日 → 红；
  - 退市（status=D）豁免；上市 <30 天观察；
  - 负向：合成 5 只日频标的回拨到 09-18 必须红；2 月不得崩溃。
"""

from datetime import datetime

import pandas as pd

import fetch.backfill as bf
from common.calendar import open_dates_by_market
from config.checkpoint_registry import domains_for
from tools.db.per_code import (
    COHORT_DAILY,
    COHORT_DELISTED,
    COHORT_HALF_YEAR,
    COHORT_NEW,
    COHORT_WEEKLY,
    VERDICT_EXEMPT,
    VERDICT_WATCH,
    _classify,
    _verdict,
    build_report,
)

# 09-18 之后到 09-29 共 6 个交易日（09-25 中秋休市），恰好触发日频 >5 红档
ASOF = "20260929"


def _dates(start, end):
    return open_dates_by_market("SSE", start_date=start, end_date=end)


def _make_root(tmp_path):
    """合成 fund_share + fund_basic：覆盖日频/周频/半年/退市/新上市。"""
    stopped = [f"51000{i}.SH" for i in range(1, 6)]
    all_daily = _dates("20260101", "20260918")
    rows = []
    for code in stopped:
        rows += [(code, d) for d in all_daily]
    rows += [("510300.SH", d) for d in _dates("20260101", ASOF)]
    rows += [("510500.SH", d) for d in _dates("20260101", ASOF)[::5]]
    rows += [("161001.SZ", "20260630")]
    rows += [("159999.SZ", d) for d in _dates("20260101", "20260918")]
    rows += [("510700.SH", d) for d in _dates("20260910", ASOF)]
    df = pd.DataFrame(rows, columns=["ts_code", "trade_date"])
    df["fd_share"] = 1.0
    (tmp_path / "fund_share").mkdir()
    df.to_parquet(tmp_path / "fund_share" / "2026.parquet", index=False)

    basic = pd.DataFrame(
        {
            "ts_code": ["159999.SZ", "510700.SH"],
            "status": ["D", "L"],
            "list_date": ["20150101", "20260910"],
        }
    )
    basic.to_parquet(tmp_path / "fund_basic.parquet", index=False)
    return stopped


def test_daily_codes_rolled_back_to_0918_must_be_red(tmp_path):
    """负向（T6 硬判据）：5 只日频标的回拨到 09-18（滞后 6 交易日）必须红。"""
    stopped = _make_root(tmp_path)
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)

    reds = {r["ts_code"]: r for r in rep["red"]}
    assert set(reds) == set(stopped), f"应恰有 5 只红：{sorted(reds)}"
    for code in stopped:
        assert reds[code]["cohort"] == COHORT_DAILY, f"{code} 应判日频（停更前自身 102 日节奏）"
        assert reds[code]["lag_td"] == 6
    assert rep["summary"]["red"] == 5


def test_cohorts_and_exemptions(tmp_path):
    _make_root(tmp_path)
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    rows = {r["ts_code"]: r for r in rep["rows"]}
    assert rows["510300.SH"]["cohort"] == COHORT_DAILY
    assert rows["510300.SH"]["verdict"] == "正常"
    assert rows["510500.SH"]["cohort"] == COHORT_WEEKLY, "每 5 个交易日披露的码应归周频档"
    assert rows["161001.SZ"]["cohort"] == COHORT_HALF_YEAR
    assert rows["159999.SZ"]["cohort"] == COHORT_DELISTED
    assert rows["159999.SZ"]["verdict"] == VERDICT_EXEMPT
    assert rows["510700.SH"]["cohort"] == COHORT_NEW
    assert rows["510700.SH"]["verdict"] == VERDICT_WATCH
    assert rep["summary"]["red"] == 5
    assert rep["summary"]["yellow"] == 0


def test_half_year_red_only_in_disclosure_window(tmp_path):
    """半年披露：9 月不报红；1 月 31 日后且 ≥90 天 → 红。"""
    (tmp_path / "fund_share").mkdir()
    pd.DataFrame(
        {"ts_code": ["161001.SZ"], "trade_date": ["20260630"], "fd_share": [1.0]}
    ).to_parquet(tmp_path / "fund_share" / "2026.parquet", index=False)
    pd.DataFrame({"ts_code": ["161001.SZ"], "status": ["L"], "list_date": ["20150101"]}).to_parquet(
        tmp_path / "fund_basic.parquet", index=False
    )

    sep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    assert sep["summary"]["red"] == 0, "9 月不在披露窗口，半年披露不应红"

    jan = build_report(
        table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof="20270131"
    )
    assert jan["summary"]["red"] == 1, "1/7 月 +30 天后且 ≥90 天未披露必须红"


def test_report_json_serializable(tmp_path):
    import json

    _make_root(tmp_path)
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    s = json.dumps(rep, ensure_ascii=False)
    assert '"summary"' in s and '"worst"' in s


def test_fund_share_t_plus_1_empty_day_is_pending(monkeypatch):
    """P4：fund_share 当日空返回不得记 done（未加入 _T1_PUBLISH_TABLES）。"""
    assert "fund_share" not in bf._T1_PUBLISH_TABLES

    class _CP:
        def __init__(self):
            self.done = []

        def mark_done(self, *a, **k):
            self.done.append((a, k))

    cp = _CP()
    today = datetime.now().strftime("%Y%m%d")
    label = bf._resolve_empty_date("fund_share", today, cp, {"n": 0}, kind="date")
    assert label == "pending"
    assert cp.done == [], "当日空不得写 completed"


def test_fund_share_has_no_code_key_space_domain():
    """P4：反向域断言 —— 防止未来重新引入 by_code 双通道。"""
    assert domains_for("fund_share", library="market_data", key_space="code") == []


# ============================================================
# R1–R4（00 §13.2 / 06 §三）：返工用例
# ============================================================
def test_february_does_not_crash(tmp_path):
    """R1：`asof=20260228` 不得构造非法日期（原 date(year,2,30) 必崩）。"""
    from datetime import date

    assert _verdict(COHORT_HALF_YEAR, 0, date(2026, 6, 30), date(2026, 2, 28)) == "正常"

    (tmp_path / "fund_share").mkdir()
    pd.DataFrame(
        {"ts_code": ["161001.SZ"], "trade_date": ["20251231"], "fd_share": [1.0]}
    ).to_parquet(tmp_path / "fund_share" / "2025.parquet", index=False)
    rep = build_report(
        table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof="20260228"
    )
    assert rep["summary"]["red"] == 0


def _write_single(tmp_path, code, dates, fund_type):
    (tmp_path / "fund_share").mkdir(exist_ok=True)
    pd.DataFrame({"ts_code": [code] * len(dates), "trade_date": dates, "fd_share": 1.0}).to_parquet(
        tmp_path / "fund_share" / "2026.parquet", index=False
    )
    pd.DataFrame({"ts_code": [code], "fund_type": [fund_type]}).to_parquet(
        tmp_path / "fund_basic.parquet", index=False
    )


def test_non_reit_95_days_lag4_is_yellow(tmp_path):
    """R2：非 REIT 的 90–96 档归日频（保守）→ 滞后 4 交易日必须黄。"""
    dates = _dates("20260101", "20260922")[-95:]
    _write_single(tmp_path, "510900.SH", dates, "股票型")
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    row = rep["rows"][0]
    assert row["cohort"] == COHORT_DAILY
    assert row["lag_td"] == 4
    assert row["verdict"] == "黄"


def test_reit_95_days_lag4_is_normal(tmp_path):
    """R2：REIT 的 10–96 档归周频（滞后 2–4 为常态）→ 同一滞后 4 应正常。"""
    dates = _dates("20260101", "20260922")[-95:]
    _write_single(tmp_path, "508001.SH", dates, "REITs")
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    row = rep["rows"][0]
    assert row["cohort"] == COHORT_WEEKLY
    assert row["lag_td"] == 4
    assert row["verdict"] == "正常"


def test_reit_suffix_fallback_when_fund_type_missing(tmp_path):
    """R2：fund_type 缺失时回落后缀 508/180 仍识别为 REIT。"""
    dates = _dates("20260101", "20260922")[-95:]
    _write_single(tmp_path, "180101.SZ", dates, "")
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    assert rep["rows"][0]["cohort"] == COHORT_WEEKLY


def test_classify_boundaries():
    assert _classify(97, is_reit=True) == COHORT_DAILY
    assert _classify(96, is_reit=True) == COHORT_WEEKLY
    assert _classify(95, is_reit=False) == COHORT_DAILY
    assert _classify(89, is_reit=False) == COHORT_WEEKLY
    assert _classify(9, is_reit=False) == COHORT_HALF_YEAR


def test_worst_red_first_even_with_many_half_year(tmp_path):
    """R3：1 红 + 500 只半年正常 → 红必须仍在 worst 首位。"""
    (tmp_path / "fund_share").mkdir()
    rows = []
    red_dates = _dates("20260101", "20260918")
    rows += [("510001.SH", d) for d in red_dates]
    for i in range(500):
        rows.append((f"16{i:04d}.SZ", "20260630"))
    pd.DataFrame(rows, columns=["ts_code", "trade_date"]).assign(fd_share=1.0).to_parquet(
        tmp_path / "fund_share" / "2026.parquet", index=False
    )
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    assert rep["worst"][0]["ts_code"] == "510001.SH"
    assert rep["worst"][0]["verdict"] == "红"


def test_asof_truncates_future_rows(tmp_path):
    """S1：asof 必须按真快照截断 —— 含 20261127 未来行、asof=20260929 时取 09-18。"""
    dates = [*_dates("20260101", "20260918"), "20261127"]
    _write_single(tmp_path, "510001.SH", dates, "股票型")
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    row = rep["rows"][0]
    assert row["latest"] == "20260918", "不得读到 asof 之后的行"
    assert row["lag_td"] == 6
    assert row["verdict"] == "红"


def test_asof_skips_codes_without_history(tmp_path):
    """S1：asof 前无数据的码跳过并计数（不得用未来数据伪造 latest）。"""
    (tmp_path / "fund_share").mkdir()
    rows = [("510300.SH", d) for d in _dates("20260101", ASOF)]
    rows.append(("510301.SH", "20261127"))
    pd.DataFrame(rows, columns=["ts_code", "trade_date"]).assign(fd_share=1.0).to_parquet(
        tmp_path / "fund_share" / "2026.parquet", index=False
    )
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    assert rep["skipped_no_data_asof"] == 1
    assert [r["ts_code"] for r in rep["rows"]] == ["510300.SH"]


def test_reit_nan_fund_type_falls_back_to_suffix(tmp_path):
    """S2：fund_type=NaN 必须回落后缀，不得判成非 REIT。"""
    from tools.db.per_code import _is_reit

    assert _is_reit("508001.SH", float("nan")) is True
    dates = _dates("20260101", "20260922")[-95:]
    _write_single(tmp_path, "508002.SH", dates, float("nan"))
    rep = build_report(table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof=ASOF)
    assert rep["rows"][0]["cohort"] == COHORT_WEEKLY


def test_dashed_asof_equals_compact(tmp_path):
    """S5（08 P3-1）：`2026-11-27` 与 `20261127` 必须完全等价（不得静默错快照）。"""
    _make_root(tmp_path)
    compact = build_report(
        table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof="20261127"
    )
    dashed = build_report(
        table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof="2026-11-27"
    )
    assert dashed["summary"] == compact["summary"]
    assert dashed["cohorts"] == compact["cohorts"]
    assert dashed["verdicts"] == compact["verdicts"]
    assert dashed["skipped_no_data_asof"] == compact["skipped_no_data_asof"]
    assert dashed["rows"] == compact["rows"]
    assert max(r["latest"] for r in dashed["rows"]) == max(r["latest"] for r in compact["rows"])


def test_nan_keys_do_not_create_pseudo_codes(tmp_path):
    """R4：trade_date/ts_code 为 'nan' 的行不得生成伪标的。"""
    (tmp_path / "fund_share").mkdir()
    pd.DataFrame(
        {
            "ts_code": ["510300.SH", "nan", "510301.SH"],
            "trade_date": ["20261127", "20261127", "nan"],
            "fd_share": [1.0, 1.0, 1.0],
        }
    ).to_parquet(tmp_path / "fund_share" / "2026.parquet", index=False)
    rep = build_report(
        table="fund_share", root=str(tmp_path), meta_dir=str(tmp_path), asof="20261127"
    )
    assert [r["ts_code"] for r in rep["rows"]] == ["510300.SH"]
