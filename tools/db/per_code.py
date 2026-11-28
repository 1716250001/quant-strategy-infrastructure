# -*- coding: utf-8 -*-
"""tools/db/per_code.py — 逐标的最新日期体检（REQ-20261126-01 T6）

回答一个问题：**表级 max 正常，是否仍有大批标的自身停更？**

背景（2026-11-28 `fund_share` 事故）
------------------------------------
表级 `max(trade_date)=20261116` 被 3 只新上市码抬到"看似正常"，而 2,271 只里
1,761 只停在 `20260918`。`db freshness` 的目录级双条件、`doctor` 核心表清单、
`check-coverage --history`（只看起点）都覆盖不到这种"总体尚新、局部大面积陈旧"，
故必须逐标的统计 `max(trade_date)`。

分档（按实测披露节奏，T6 派单）
------------------------------
以每只标的**自身披露节奏**分档（窗口 = 102 个交易日；00 §13.2.2 裁定）：

    日频（非 REIT）  n ≥ 90       滞后 > 3 黄 / > 5 红（交易日）
    日频（REIT）     n ≥ 97       异常密集，按日频严查
    周频/REIT（REIT）10 ≤ n ≤ 96  滞后 > 10 黄 / > 20 红（交易日）
    周频（非 REIT）  10 ≤ n ≤ 89  同上
    半年披露         n < 10       仅在披露窗口（1/7 月）+30 天后、且 ≥90 自然日 → 红

> 90–96 档的 REIT 归周频（滞后 2–4 为常态），非 REIT 归日频（保守）；
> REIT 用 `fund_basic.fund_type` 识别，缺失时回落后缀 508/180。

⚠ **窗口右端取"自身最新披露日"与 asof 二者的大值**（即 `max(n_asof, n_own)`）：
  停更 45 天的日频标的，在"截至 asof 的 102 日窗口"里只剩 ~57 天，会被误降为
  周频档（阈值 >20 虽仍能报红，但 cohort 失真、且停更 6~10 天时会漏报）。
  以其停更前自身的 102 日为窗口，才能稳定识别"日频→突然停更"。

特殊豁免
--------
- 退市（`fund_basic.status == "D"`）→ 「豁免」，不参与红黄统计；
- 上市 < 30 自然日 → 「观察」，不参与红黄统计（新码首次收录有滞后）。

⚠ 本模块**只读**：不写数据、不写断点、不建数据目录。

用法
----
    python main.py db freshness --per-code
    python main.py db freshness --per-code --asof 20260928 --json
    python main.py db freshness --per-code --table fund_share --json-out out.json
"""

import bisect
import glob
import json
import os
from datetime import datetime

import pandas as pd

from common.calendar import open_dates_by_market
from config import MARKET_DATA_DIR, META_DIR

WINDOW_DAYS = 102
DAILY_MIN_DAYS = 97
WEEKLY_MIN_DAYS = 10
YELLOW_DAILY_LAG = 3
RED_DAILY_LAG = 5
YELLOW_WEEKLY_LAG = 10
RED_WEEKLY_LAG = 20
HALF_YEAR_RED_AGE_DAYS = 90
NEW_LISTING_DAYS = 30

COHORT_DAILY = "日频"
COHORT_WEEKLY = "周频/REIT"
COHORT_HALF_YEAR = "半年披露"
COHORT_DELISTED = "退市豁免"
COHORT_NEW = "新上市观察"

VERDICT_RED = "红"
VERDICT_YELLOW = "黄"
VERDICT_OK = "正常"
VERDICT_WATCH = "观察"
VERDICT_EXEMPT = "豁免"


def _parse_date(v):
    """'20260918' / '2026-09-18' / Timestamp / nan → date | None。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none", "<na>"):
        return None
    if len(s) >= 10 and s[4] in "-/":
        s = s[:10]
    try:
        return datetime.strptime(s.replace("-", "").replace("/", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _load_fund_basic(meta_dir):
    """读基金名录（status/list_date/fund_type）；缺失返回空 DataFrame。"""
    cols = ["ts_code", "status", "list_date", "fund_type"]
    path = os.path.join(meta_dir, "fund_basic.parquet")
    if not os.path.isfile(path):
        print(f"[per-code] 缺少 {path}：退市/新上市/REIT 分档不生效")
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        print(f"[per-code] 读 fund_basic 失败（豁免不生效）: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]


def _read_table(table, root):
    """读该表全部分区的 (ts_code, trade_date)，按代码分组为升序日期列表。"""
    files = sorted(glob.glob(os.path.join(root, table, "*.parquet")))
    if not files:
        return {}, files
    frames = []
    for fp in files:
        try:
            frames.append(pd.read_parquet(fp, columns=["ts_code", "trade_date"]))
        except Exception as exc:
            print(f"[per-code] 分片读取失败 {fp}: {type(exc).__name__}: {exc}")
    if not frames:
        return {}, files
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["ts_code", "trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    df["trade_date"] = df["trade_date"].astype(str)
    # R4：astype(str) 会把 NaN 变成 "nan"（dropna 已失效），须显式剔除，
    #   否则会凭空多出 ts_code="nan"/trade_date="nan" 的伪标的。
    _bad = {"nan", "nat", "none", "<na>", ""}
    df = df[~df["ts_code"].str.lower().isin(_bad) & ~df["trade_date"].str.lower().isin(_bad)]
    grouped = df.groupby("ts_code")["trade_date"].agg(lambda s: sorted(set(s)))
    return grouped.to_dict(), files


def _default_asof():
    today = datetime.now().strftime("%Y%m%d")
    dates = open_dates_by_market("SSE", end_date=today)
    return dates[-1] if dates else today


def _is_reit(code, fund_type):
    """REIT 判定：优先 fund_type，缺失/NaN 时回落后缀 508/180（00 §13.2.2 / §14.2 S2）。"""
    ft = "" if fund_type is None else str(fund_type).strip()
    if ft.lower() in ("", "nan", "nat", "none", "<na>"):
        return str(code)[:3] in ("508", "180")
    return ft.upper() in ("REITS", "REIT")


def _classify(n_days, is_reit=False):
    """按披露节奏分档（00 §13.2.2 裁定）。

    - REIT：n≥97 → 日频（异常密集，严查）；10–96 → 周频/REIT；<10 → 半年；
    - 非 REIT：n≥90 → 日频（保守，覆盖 90–96 的准日频标的）；10–89 → 周频；<10 → 半年。
    """
    if is_reit:
        if n_days >= DAILY_MIN_DAYS:
            return COHORT_DAILY
        if n_days >= WEEKLY_MIN_DAYS:
            return COHORT_WEEKLY
        return COHORT_HALF_YEAR
    if n_days >= 90:
        return COHORT_DAILY
    if n_days >= WEEKLY_MIN_DAYS:
        return COHORT_WEEKLY
    return COHORT_HALF_YEAR


def _verdict(cohort, lag_td, latest_date, asof_date):
    if cohort == COHORT_DAILY:
        if lag_td > RED_DAILY_LAG:
            return VERDICT_RED
        if lag_td > YELLOW_DAILY_LAG:
            return VERDICT_YELLOW
        return VERDICT_OK
    if cohort == COHORT_WEEKLY:
        if lag_td > RED_WEEKLY_LAG:
            return VERDICT_RED
        if lag_td > YELLOW_WEEKLY_LAG:
            return VERDICT_YELLOW
        return VERDICT_OK
    # 半年披露：只在披露窗口（1/7 月）+30 天后，且距最新披露 ≥90 自然日 → 红。
    # R1：**不得**构造 date(year, month, 30) —— 2 月/小月会 ValueError（06 §3.2）。
    in_window = asof_date.month in (1, 7) and asof_date.day > 30
    too_old = (asof_date - latest_date).days >= HALF_YEAR_RED_AGE_DAYS
    if in_window and too_old:
        return VERDICT_RED
    return VERDICT_OK


def build_report(table="fund_share", root=None, meta_dir=None, asof=None, top=20):
    """生成逐标的体检报告（纯函数，便于测试）。

    Returns: dict（JSON 可序列化），字段见模块 docstring。
    """
    root = root or MARKET_DATA_DIR
    meta_dir = meta_dir or META_DIR
    asof = str(asof) if asof else _default_asof()
    asof_date = _parse_date(asof)
    if asof_date is None:
        raise ValueError(f"asof 非法: {asof!r}")
    # S5（REQ-20261126-01-F2-R3）：统一归一化为 8 位再用于字符串截断/日历查询。
    #   否则 "2026-11-27" 会因 '1' > '-' 把 2026 年行全部丢弃、日历截到 20251231，
    #   静默产出一份“多数正常”的假快照（08 P3-1）。
    asof = asof_date.strftime("%Y%m%d")

    by_code, files = _read_table(table, root)
    all_dates = open_dates_by_market("SSE", end_date=asof)
    window = all_dates[-WINDOW_DAYS:]
    window_start = window[0] if window else asof
    fund_basic = _load_fund_basic(meta_dir)
    meta = {}
    for row in fund_basic.itertuples(index=False):
        meta[str(row.ts_code)] = (
            str(row.status) if row.status is not None else "",
            row.list_date,
            row.fund_type,
        )

    rows = []
    skipped_no_data = 0
    for code, dates in by_code.items():
        # S1（00 §14.2）：asof 是**真快照日** —— 先截断该标的 >asof 的行，
        #   否则历史回看会读到未来数据（07 P2：asof=20260228 却看到 11 月行情）。
        dates = [d for d in dates if str(d) <= asof]
        if not dates:
            skipped_no_data += 1
            continue
        latest = dates[-1]
        latest_date = _parse_date(latest) or asof_date
        i = bisect.bisect_right(all_dates, latest) - 1
        if i < 0:
            i = 0
        lag_td = max(0, len(all_dates) - 1 - i)
        n_asof = bisect.bisect_right(dates, asof) - bisect.bisect_left(dates, window_start)
        own_start = all_dates[max(0, i - (WINDOW_DAYS - 1))]
        n_own = bisect.bisect_right(dates, latest) - bisect.bisect_left(dates, own_start)
        status, list_date, fund_type = meta.get(code, ("", None, None))
        cohort = _classify(max(n_asof, n_own), _is_reit(code, fund_type))
        list_d = _parse_date(list_date)
        if status == "D":
            cohort_show, verdict = COHORT_DELISTED, VERDICT_EXEMPT
        elif list_d is not None and (asof_date - list_d).days < NEW_LISTING_DAYS:
            cohort_show, verdict = COHORT_NEW, VERDICT_WATCH
        else:
            cohort_show = cohort
            verdict = _verdict(cohort, lag_td, latest_date, asof_date)

        rows.append(
            {
                "ts_code": code,
                "cohort": cohort_show,
                "latest": latest,
                "lag_td": lag_td,
                "verdict": verdict,
            }
        )

    order = {
        COHORT_DAILY: 0,
        COHORT_WEEKLY: 1,
        COHORT_HALF_YEAR: 2,
        COHORT_DELISTED: 3,
        COHORT_NEW: 4,
    }
    rows.sort(key=lambda r: (order.get(r["cohort"], 9), r["ts_code"]))
    cohorts = {
        c: sum(1 for r in rows if r["cohort"] == c)
        for c in (COHORT_DAILY, COHORT_WEEKLY, COHORT_HALF_YEAR, COHORT_DELISTED, COHORT_NEW)
    }
    verdicts = {
        v: sum(1 for r in rows if r["verdict"] == v)
        for v in (VERDICT_RED, VERDICT_YELLOW, VERDICT_OK, VERDICT_WATCH, VERDICT_EXEMPT)
    }
    # R3：worst 排除观察/豁免，红/黄优先、再按滞后倒序 ——
    #   否则 472 只半年披露（lag 37 但正常）会把真正的日频红黄挤出前 20。
    _severity = {VERDICT_RED: 0, VERDICT_YELLOW: 1, VERDICT_OK: 2}
    worst = sorted(
        (r for r in rows if r["verdict"] not in (VERDICT_EXEMPT, VERDICT_WATCH)),
        key=lambda r: (_severity.get(r["verdict"], 9), -r["lag_td"], r["ts_code"]),
    )[:top]

    return {
        "ok": True,
        "table": table,
        "asof": asof,
        "partitions": len(files),
        "codes": len(rows),
        "skipped_no_data_asof": skipped_no_data,
        "thresholds": {
            "window_days": WINDOW_DAYS,
            "daily": {
                "min_days": DAILY_MIN_DAYS,
                "yellow_lag": YELLOW_DAILY_LAG,
                "red_lag": RED_DAILY_LAG,
            },
            "weekly": {
                "min_days": WEEKLY_MIN_DAYS,
                "yellow_lag": YELLOW_WEEKLY_LAG,
                "red_lag": RED_WEEKLY_LAG,
            },
            "half_year": {"red_age_days": HALF_YEAR_RED_AGE_DAYS, "month": [1, 7], "after_day": 30},
            "new_listing_days": NEW_LISTING_DAYS,
        },
        "summary": {"red": verdicts[VERDICT_RED], "yellow": verdicts[VERDICT_YELLOW]},
        "cohorts": cohorts,
        "verdicts": verdicts,
        "worst": worst,
        "rows": rows,
        "red": [r for r in rows if r["verdict"] == VERDICT_RED],
        "yellow": [r for r in rows if r["verdict"] == VERDICT_YELLOW],
    }


def render(report):
    th = report["thresholds"]
    _skip = report.get("skipped_no_data_asof") or 0
    lines = [
        f"[per-code] {report['table']} | asof={report['asof']} | "
        f"{report['codes']} 只 / {report['partitions']} 分区 | 窗口={th['window_days']} 交易日"
        + (f" | asof 前无数据 {_skip} 只（跳过）" if _skip else ""),
        "  分档: " + " | ".join(f"{k} {v}" for k, v in report["cohorts"].items()),
        "  判定: " + " | ".join(f"{k} {v}" for k, v in report["verdicts"].items()),
        (
            f"  阈值: 日频 >{th['daily']['yellow_lag']}黄/> {th['daily']['red_lag']}红(交易日) | "
            f"周频 >{th['weekly']['yellow_lag']}黄/> {th['weekly']['red_lag']}红(交易日) | "
            f"半年 仅{th['half_year']['month']}月+{th['half_year']['after_day']}天后"
            f"≥{th['half_year']['red_age_days']}自然日红 | 新上市<{th['new_listing_days']}日观察"
        ),
        f"  worst {len(report['worst'])}（按滞后交易日倒序）:",
        f"    {'代码':14s} {'分档':10s} {'最新日':10s} {'滞后':>4s}  判定",
    ]
    for r in report["worst"]:
        lines.append(
            f"    {r['ts_code']:14s} {r['cohort']:10s} {r['latest']:10s} {r['lag_td']:>4d}  {r['verdict']}"
        )
    if report["red"]:
        lines.append("  红: " + ", ".join(r["ts_code"] for r in report["red"][:20]))
    if report["yellow"]:
        lines.append("  黄: " + ", ".join(r["ts_code"] for r in report["yellow"][:20]))
    return "\n".join(lines)


def run(table="fund_share", asof=None, as_json=False, json_out=None, top=20, strict=True):
    """CLI 入口：文本/JSON 输出；strict 时红>0 → exit 1。"""
    try:
        report = build_report(table=table, asof=asof, top=top)
    except Exception as exc:
        print(f"[per-code] 生成失败: {type(exc).__name__}: {exc}")
        return 2
    if json_out:
        d = os.path.dirname(os.path.abspath(json_out))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render(report))
    return 1 if strict and report["summary"]["red"] > 0 else 0
