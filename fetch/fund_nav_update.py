# -*- coding: utf-8 -*-
"""
fetch/fund_nav_update.py — 基金净值增量更新 (fund_nav)
======================================================
按 nav_date 回溯拉取最近 N 个交易日的全市场基金净值
（场外 .OF + 场内 ETF/LOF），写入 fund_nav/{YYYY}.parquet（按年分区）。

设计原理:
  - fund_nav 的日期列是 nav_date，按年分区，行级主键 (ts_code, nav_date)
  - 「首次收录」判定：fund_nav 已迁为按年分区，无法再用
    "{ts_code}.parquet 是否存在" 判断，改为查库内近两年出现的代码集合
  - 基金净值 T 日约 20:00 后才陆续公布（QDII/货基更晚），
    17:30 主流程运行时当日净值尚未齐全，因此每次回溯拉取
    最近 N 个交易日，自动补上延迟公布与漏拉的数据（不丢数）
  - tushare fund_nav 单次返回上限 5000 行，全市场一天
    15000+ 条，需 limit/offset 分页

用法:
  python -m fetch.fund_nav_update                 # 回溯最近5个交易日
  python -m fetch.fund_nav_update --days 10       # 回溯10个交易日
  python -m fetch.fund_nav_update --dry-run       # 空跑，只统计不写入
"""

import os
import time
import argparse
from datetime import datetime

import pandas as pd

from config import (
    FUND_NAV_DIR, FUND_NAV_LOOKBACK_DAYS, FUND_NAV_PAGE_SIZE,
    META_DIR, INTRADAY_RATE_INTERVAL,
)
from common.calendar import recent_trade_dates
from common import reader
from common.parquet_store import merge_append, upsert_by_year, list_parquet_files
from fetch.base import (
    get_pro, RateLimiter, ensure_dir, ts_call_with_retry, ts_fetch,
)


def _known_codes(recent_years=2):
    """库内近 N 年出现过的基金代码（轻量：只读最近几个年份文件的 ts_code 列）。

    用途: 判断"首次收录"。历史实现靠判断 {ts_code}.parquet 是否存在，
    全库改为按年分区后该判据不再成立。用"近两年文件里是否出现过"近似，
    对判断新收录已足够（新入库的基金必不在近两年文件中）。
    """
    files = sorted(list_parquet_files(FUND_NAV_DIR))[-recent_years:]
    codes = set()
    for f in files:
        try:
            s = pd.read_parquet(os.path.join(FUND_NAV_DIR, f),
                                columns=["ts_code"])["ts_code"]
            codes.update(s.dropna().unique().tolist())
        except Exception:
            pass
    return codes


# ============================================================
# 交易日历
# ============================================================

def get_recent_trade_dates(n, end_date=None):
    """
    从本地 trade_cal 取最近 n 个交易日 (升序)。

    注意: trade_cal 覆盖到 2030 年，含未来交易日，
    必须以"今天"为上限，否则会取到未来日期。

    实现已统一至 common.calendar.recent_trade_dates。
    """
    return recent_trade_dates(n, end_date=end_date)


# ============================================================
# 分页拉取
# ============================================================

def fetch_nav_by_date(pro, limiter, nav_date, page_size=FUND_NAV_PAGE_SIZE):
    """按 nav_date 分页拉取全市场净值 (单次上限5000行)"""
    chunks = []
    offset = 0
    while True:
        params = {"nav_date": nav_date, "limit": page_size, "offset": offset}
        df = ts_call_with_retry(pro, "fund_nav", limiter, params)
        if df is None or df.empty:
            break
        chunks.append(df)
        if len(df) < page_size:
            break
        offset += page_size

    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


def fetch_nav_full_history(pro, limiter, ts_code, max_rows=FUND_NAV_PAGE_SIZE):
    """
    按 ts_code 拉取单只基金的全历史净值（含 5000 行截断续拉）。
    用于首次收录某只基金时补全历史，避免只留下窗口内的几天数据。
    """
    df, ok = ts_fetch(pro, "fund_nav", limiter, ts_code, max_rows=max_rows)
    if not ok or df is None or df.empty:
        return pd.DataFrame()
    return df


def load_of_scope():
    """
    加载允许收录的场外基金代码集合。

    收录口径（固定规则）:
      - 场外 .OF: 仅 股票型 + 混合型（与既有分析口径一致）
      - 场内 .SH/.SZ/.BJ: 全部收录（ETF/LOF 净值，可用于折溢价）

    返回:
        set of ts_code（仅含允许的场外代码；场内不在此集合限制范围内）
        若元数据缺失则返回 None（表示不做过滤）
    """
    fb_path = os.path.join(META_DIR, "fund_basic.parquet")
    if not os.path.exists(fb_path):
        print("  [WARN] fund_basic.parquet 不存在，跳过口径过滤")
        return None
    fb = pd.read_parquet(fb_path, columns=["ts_code", "fund_type"])
    fb = fb.drop_duplicates(subset="ts_code")
    is_of = fb["ts_code"].str.endswith(".OF")
    ok = is_of & fb["fund_type"].isin(["股票型", "混合型"])
    return set(fb.loc[ok, "ts_code"])


def _in_scope(ts_code, allowed_of):
    """判断代码是否在收录口径内（场内全部；场外需在 allowed_of 中）"""
    if not ts_code.endswith(".OF"):
        return True
    if allowed_of is None:
        return True
    return ts_code in allowed_of


# ============================================================
# 主流程
# ============================================================

def run_fund_nav_update(dry_run=False, lookback_days=None, end_date=None):
    """
    fund_nav 增量更新主流程。

    参数:
        dry_run: 空跑模式，只拉取统计不写入
        lookback_days: 回溯交易日数 (默认 config.FUND_NAV_LOOKBACK_DAYS)
        end_date: 回溯截止日 YYYYMMDD (默认: 最新)

    返回:
        (updated_files, new_rows)
    """
    if lookback_days is None:
        lookback_days = FUND_NAV_LOOKBACK_DAYS

    print("=" * 60)
    print("  基金净值增量更新 (fund_nav)")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  存储: {os.path.abspath(FUND_NAV_DIR)}")
    if dry_run:
        print("  模式: DRY-RUN (只统计不写入)")
    print("=" * 60)

    ensure_dir(FUND_NAV_DIR)

    dates = get_recent_trade_dates(lookback_days, end_date)
    if not dates:
        print("  [ERROR] 未取到交易日，请检查 metadata/trade_cal.parquet")
        return 0, 0
    print(f"  回溯窗口: 最近 {len(dates)} 个交易日 ({dates[0]} ~ {dates[-1]})")

    pro = get_pro()
    limiter = RateLimiter(
        rate_per_min=int(60.0 / INTRADAY_RATE_INTERVAL),
        daily_limit=90000,
    )

    # ---- 拉取回溯窗口内所有净值 ----
    chunks = []
    for i, d in enumerate(dates):
        df = fetch_nav_by_date(pro, limiter, d)
        if df is None or df.empty:
            print(f"  [{i+1}/{len(dates)}] {d}: 无数据")
            continue
        codes = df["ts_code"].fillna("")
        n_of = int(codes.str.endswith(".OF").sum())
        print(f"  [{i+1}/{len(dates)}] {d}: {len(df)}行 (场外{n_of} / 场内{len(df)-n_of})")
        chunks.append(df)

    if not chunks:
        print("\n  [WARN] 回溯窗口内未拉取到任何净值数据")
        return 0, 0

    alldf = pd.concat(chunks, ignore_index=True)
    alldf = alldf.drop_duplicates(subset=["ts_code", "nav_date"], keep="last")

    # 口径过滤: 场外仅股票型+混合型, 场内全部
    allowed_of = load_of_scope()
    if allowed_of is not None:
        before = len(alldf)
        mask = (~alldf["ts_code"].str.endswith(".OF")) | alldf["ts_code"].isin(allowed_of)
        alldf = alldf[mask]
        dropped = before - len(alldf)
        if dropped:
            n_of = int(alldf["ts_code"].str.endswith(".OF").sum())
            print(f"  口径过滤: 剔除 {dropped} 行 (场外非股票型/混合型) | 保留 场外{n_of} / 场内{len(alldf)-n_of}")

    n_codes = alldf["ts_code"].nunique()
    print(f"\n  合计: {len(alldf)}行 | {n_codes}只基金")
    print(f"  请求统计: {limiter.status_str()}")

    if dry_run:
        print("\n  [DRY-RUN] 跳过写入")
        return 0, 0

    # ---- 落盘：按年分区写入 ----
    # fund_nav 已迁移为 by_year 布局，不能再按 ts_code 拆文件
    # （旧写法会在年度目录里生成数万个小文件，并把布局探测打回 by_code）。
    print("\n  写入 fund_nav/ (按年分区) ...")
    t0 = time.time()

    # 首次收录的基金：补拉全历史，避免只留窗口内几天
    known = _known_codes()
    new_codes = sorted(set(alldf["ts_code"].unique()) - known)
    if new_codes:
        print(f"  首次收录 {len(new_codes)} 只，补拉全历史...")
        hist_frames = []
        for i, code in enumerate(new_codes):
            if (i + 1) % 200 == 0:
                print(f"    [{i+1}/{len(new_codes)}] 已补 {len(hist_frames)} 只")
            hist = fetch_nav_full_history(pro, limiter, code)
            if hist is not None and not hist.empty:
                hist_frames.append(hist)
        if hist_frames:
            alldf = pd.concat([alldf] + hist_frames, ignore_index=True)
            alldf = alldf.drop_duplicates(subset=["ts_code", "nav_date"], keep="last")

    stat = upsert_by_year(alldf, FUND_NAV_DIR, date_col="nav_date",
                          subset=["ts_code", "nav_date"], sort_by="nav_date",
                          on_error=lambda y, e: print(f"    [ERROR] 分区 {y}: {e}"))
    elapsed = time.time() - t0
    new_rows = stat["added"]
    updated = stat["partitions"]
    new_files = len(new_codes)
    unchanged = max(0, stat["partitions"] - updated)
    fail = stat["fail"]
    print(f"\n{'=' * 60}")
    print("  基金净值更新完成 — 统计报告")
    print(f"{'=' * 60}")
    print(f"  回溯窗口:       {dates[0]} ~ {dates[-1]} ({len(dates)}个交易日)")
    print(f"  拉取数据:       {len(alldf)}行 / {n_codes}只")
    print(f"  写入年份分区:   {updated} 个 (首次收录 {new_files} 只)")
    print(f"  失败分区:       {fail}")
    print(f"  新增行数:       {new_rows}")
    print(f"  总请求:         {limiter.total_requests}")
    print(f"  耗时:           {elapsed:.1f}s")
    print(f"{'=' * 60}")

    return updated, new_rows


# ============================================================
# 历史回补 (repair)
# ============================================================

def repair_incomplete(threshold=5, dry_run=False, limit=None):
    """
    扫描 fund_nav 目录，对行数 <= threshold 的文件按 ts_code 补拉全历史。

    用途: 修复首次收录时只写入窗口数据、造成历史缺失的文件。

    参数:
        threshold: 行数阈值，<= 该值的文件视为不完整
        dry_run: 空跑模式
        limit: 限制本次处理数量 (调试用)

    返回:
        (fixed_count, added_rows)
    """
    print("=" * 60)
    print("  基金净值历史回补 (repair)")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  阈值: 行数 <= {threshold}")
    if dry_run:
        print("  模式: DRY-RUN (只统计不写入)")
    print("=" * 60)

    ensure_dir(FUND_NAV_DIR)
    # 按年分区后无"文件"概念，改为统计库内每只基金的行数（按年文件累加）
    counts = {}
    for _year, path in reader.iter_year_files("fund_nav"):
        try:
            s = pd.read_parquet(path, columns=["ts_code"])["ts_code"]
            for code, n in s.value_counts().items():
                counts[code] = counts.get(code, 0) + int(n)
        except Exception:
            continue
    print(f"  扫描完成: 库内 {len(counts)} 只基金")

    allowed_of = load_of_scope()
    targets = sorted(c for c, n in counts.items()
                     if n <= threshold and _in_scope(c, allowed_of))

    print(f"  待回补: {len(targets)} 只")
    if not targets:
        print("  无需回补")
        return 0, 0

    if limit:
        targets = targets[:limit]
        print(f"  限制本次处理: {len(targets)} 只")

    if dry_run:
        print(f"  [DRY-RUN] 预计请求 {len(targets)} 次")
        return 0, 0

    pro = get_pro()
    limiter = RateLimiter(
        rate_per_min=int(60.0 / INTRADAY_RATE_INTERVAL),
        daily_limit=90000,
    )

    t0 = time.time()
    fixed = 0
    fail = 0
    added = 0
    for i, ts_code in enumerate(targets):
        if (i + 1) % 200 == 0 or i == 0:
            print(f"    [{i+1}/{len(targets)}] 已回补={fixed} 新增行={added} | {time.time()-t0:.0f}s")

        hist = fetch_nav_full_history(pro, limiter, ts_code)
        if hist is None or hist.empty:
            fail += 1
            continue

        try:
            stat = upsert_by_year(hist, FUND_NAV_DIR, date_col="nav_date",
                                  subset=["ts_code", "nav_date"], sort_by="nav_date",
                                  on_error=lambda y, e: print(f"    [ERROR] {y}: {e}"))
            if stat["added"] > 0:
                added += stat["added"]
                fixed += 1
        except Exception as e:
            print(f"    [ERROR] {ts_code}: {e}")
            fail += 1

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print("  历史回补完成 — 统计报告")
    print(f"{'=' * 60}")
    print(f"  修复文件:   {fixed}")
    print(f"  失败:       {fail}")
    print(f"  新增历史行: {added}")
    print(f"  总请求:     {limiter.total_requests}")
    print(f"  耗时:       {elapsed/60:.1f}min")
    print(f"{'=' * 60}")

    return fixed, added


# ============================================================
# CLI入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="基金净值增量更新 (回溯最近N个交易日，自动补漏)")
    parser.add_argument("--days", type=int, default=None, help="回溯交易日数 (默认5)")
    parser.add_argument("--date", default=None, help="回溯截止日 YYYYMMDD (默认最新)")
    parser.add_argument("--dry-run", action="store_true", help="空跑模式，只统计不写入")
    parser.add_argument("--repair", action="store_true",
                        help="历史回补模式: 对行数过少的文件按代码补拉全历史")
    parser.add_argument("--repair-threshold", type=int, default=5,
                        help="回补阈值(行数)，默认5")
    parser.add_argument("--limit", type=int, default=None,
                        help="限制处理数量 (调试/分批用)")
    args = parser.parse_args()

    if args.repair:
        repair_incomplete(
            threshold=args.repair_threshold,
            dry_run=args.dry_run,
            limit=args.limit,
        )
        return

    run_fund_nav_update(
        dry_run=args.dry_run,
        lookback_days=args.days,
        end_date=args.date.replace("-", "") if args.date else None,
    )


if __name__ == "__main__":
    main()
