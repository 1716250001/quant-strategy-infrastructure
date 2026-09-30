# -*- coding: utf-8 -*-
"""
fetch/redtide_supply.py — 赤潮数据补全模块
============================================
按《赤潮 v7.4 数据对接需求规格书》补齐所需数据。

现役职责（2026-09-17 起收敛为 1 项）:
  market_state — 跌停状态 (stk_limit + daily close 计算, 按年分文件)

已改道（保留同名函数仅为兼容旧调用，内部转发到每日增量引擎）:
  adj_factor / stk_limit — 原按 trade_date 批量拉后**按 ts_code 逐文件写入**。
  全库已统一为按年分区（by_year），旧落盘方式会在年度目录里生成数千个小文件、
  并把 reader 的布局探测打回 by_code。现统一由 fetch/daily_update.py 执行。

  index_daily 亦不在本模块处理（同理由 daily_update 统一负责）。

CLI入口: python main.py redtide-supply [--dry-run]
"""

import os
import pandas as pd
from datetime import datetime

from config import (
    ADJ_FACTOR_DIR, STK_LIMIT_DIR,
    MARKET_STATE_DERIVED_DIR,
    STOCK_DAILY_DIR,
    REDTIDE_BOARD_PREFIX, REDTIDE_MARKET_STATE_START_YEAR,
    MARKET_DATA_DIR,
)
from common.calendar import open_dates
from common import reader
from common.parquet_store import upsert_grouped
from fetch.base import (
    get_pro, RateLimiter, Checkpoint,
    ts_fetch_by_date,
    ensure_dir, get_latest_trade_date,
)


# ============================================================
# 工具函数
# ============================================================

def _get_trade_dates(start_date, end_date):
    """交易日列表（闭区间，升序）—— 统一走 common.calendar.open_dates"""
    return open_dates(start_date, end_date)


def _get_board(ts_code):
    """根据 ts_code 前缀判断板块"""
    prefix = ts_code[:3]
    for prefixes, board in REDTIDE_BOARD_PREFIX.items():
        if prefix in prefixes:
            return board
    return "其他"


def _get_done_dates(checkpoint_name="adj_factor"):
    """从 checkpoint 读取已完成日期集合，作为断点"""
    ck = Checkpoint(data_root=MARKET_DATA_DIR)
    done_dates = ck.data.get(checkpoint_name, {}).get("done_dates", [])
    return set(done_dates)


def _save_done_dates(checkpoint_name, done_dates_set):
    """保存已完成日期到 checkpoint"""
    ck = Checkpoint(data_root=MARKET_DATA_DIR)
    if checkpoint_name not in ck.data:
        ck.data[checkpoint_name] = {"done_dates": []}
    ck.data[checkpoint_name]["done_dates"] = sorted(list(done_dates_set))
    ck.save()


# ============================================================
# Phase 1: adj_factor — 复权因子
# ============================================================

def run_adj_factor(dry_run=False, start_date="20050101"):
    """adj_factor 复权因子 —— 已改道至每日增量引擎。

    为何改道: 原实现用 upsert_grouped 按 ts_code 写 5,910 个小文件。全库已
    迁移为按年分区（by_year），旧路径会在年度目录里凭空生成数千个小文件，
    并把 reader 的布局探测打回 by_code，导致下游读数全面降级。
    现统一由 fetch/daily_update 的 by_date 路径执行（按日并发 + 批量落盘）。

    start_date 参数保留仅为兼容旧调用；实际起点取 config 里的 target 定义。
    """
    print("\n" + "=" * 60)
    print("  [Phase 1] adj_factor 复权因子（改道：每日增量引擎）")
    print("=" * 60)
    print("  ⚠ 按 ts_code 逐文件写入的旧路径已废弃（会打乱按年分区布局）")
    from fetch.daily_update import run_daily_update
    run_daily_update(dry_run=dry_run, only=["adj_factor"],
                     with_market_state=False)


# ============================================================
# Phase 3: stk_limit — 涨跌停价
# ============================================================

def run_stk_limit(dry_run=False, start_date="20080102"):
    """stk_limit 涨跌停价 —— 已改道至每日增量引擎（原因同 run_adj_factor）。"""
    print("\n" + "=" * 60)
    print("  [Phase 3] stk_limit 涨跌停价（改道：每日增量引擎）")
    print("=" * 60)
    print("  ⚠ 按 ts_code 逐文件写入的旧路径已废弃（会打乱按年分区布局）")
    from fetch.daily_update import run_daily_update
    run_daily_update(dry_run=dry_run, only=["stk_limit"],
                     with_market_state=False)


# ============================================================
# Phase 4: market_state — 跌停状态计算
# ============================================================

def run_market_state(dry_run=False, start_year=None, years=None):
    """
    用 stk_limit 的 down_limit + daily 的 close 计算跌停状态。
    输出 market_state/derived/security_state_{YYYY}.parquet (按年分文件)。

    字段: trade_date, ts_code, is_limit_down_close, board

    参数:
        years: 只处理指定年份（如 [2026]）。每日增量只需重算当年，
               避免每次扫全部历史年份。None = 从 start_year 到今年。
    """
    print("\n" + "=" * 60)
    print("  [Phase 4] market_state 跌停状态计算")
    print("=" * 60)

    ensure_dir(MARKET_STATE_DERIVED_DIR)

    current_year = datetime.now().year
    if years is not None:
        years = sorted(int(y) for y in years)
    else:
        if start_year is None:
            start_year = REDTIDE_MARKET_STATE_START_YEAR
        years = list(range(start_year, current_year + 1))
    print(f"  年份范围: {years[0]} ~ {years[-1]} (共{len(years)}年)")

    if dry_run:
        print(f"  [DRY-RUN] 预计生成 {len(years)} 个文件")
        return

    # 检查 stk_limit 是否存在（布局无关：只要目录内有 parquet 即可）
    from common.parquet_store import list_parquet_files
    if not list_parquet_files(STK_LIMIT_DIR):
        print("  [ERROR] stk_limit 目录为空，请先运行 stk_limit 拉取！")
        return

    total_rows = 0
    years_done = 0

    for year in years:
        year_start = f"{year}0101"
        year_end = f"{year}1231"

        output_path = os.path.join(MARKET_STATE_DERIVED_DIR, f"security_state_{year}.parquet")

        # 检查是否已存在
        # 历史年份: 文件存在即跳过（不可变）
        # 当前年份: 必须增量更新（stk_limit/daily 已补到最新，重算后合并去重）
        is_current_year = (year == current_year)
        if os.path.exists(output_path) and not is_current_year:
            print(f"  {year}: 已存在，跳过（删除后可重跑）")
            continue

        if os.path.exists(output_path) and is_current_year:
            # 增量更新: 读取已有数据，找出缺失日期
            try:
                existing = pd.read_parquet(output_path)
                if not existing.empty and "trade_date" in existing.columns:
                    done_dates = set(existing["trade_date"].astype(str))
                    print(f"  {year}: 已有 {len(done_dates)} 个交易日 (至 {max(done_dates)})，增量补算...")
                else:
                    existing = pd.DataFrame()
                    done_dates = set()
            except Exception:
                existing = pd.DataFrame()
                done_dates = set()
        else:
            existing = pd.DataFrame()
            done_dates = set()

        print(f"\n  处理 {year}...")

        # 获取该年交易日
        trade_dates = _get_trade_dates(year_start, year_end)
        if not trade_dates:
            print(f"    [WARN] {year} 无交易日，跳过")
            continue

        # 只补算缺失的日期（全量则重算）
        # ⚠ 必须按"今天"截断: 本地交易日历覆盖到未来（实测到 20271231），
        #   不截断会把未来交易日当成缺口（实测每天白算 69 个未来日、
        #   并因 stk_limit 无未来数据而报"无 stk_limit 数据"）。
        today = datetime.now().strftime("%Y%m%d")
        pending_dates = [d for d in trade_dates
                         if str(d) not in done_dates and str(d) <= today]
        if not pending_dates:
            print(f"    {year}: 无缺失日期，跳过")
            continue
        print(f"    待补算: {len(pending_dates)} 个交易日 ({pending_dates[0]} ~ {pending_dates[-1]})")

        # 批量处理方式:
        # 一次性扫描 stk_limit 目录所有文件，过滤出该年数据
        # 同时扫描 daily 目录对应年份数据
        # 合并计算
        # 增量模式: 只加载待补算日期区间的数据 (pending_dates[0] ~ pending_dates[-1])

        load_start = min(pending_dates)
        load_end = max(pending_dates)
        print(f"    加载 stk_limit 数据 ({load_start} ~ {load_end})...")
        stk_limit_year = _load_year_data_from_dir(STK_LIMIT_DIR, load_start, load_end)
        if stk_limit_year.empty:
            print(f"    [WARN] {year} 无 stk_limit 数据")
            continue
        print(f"    stk_limit: {len(stk_limit_year)}行, {stk_limit_year['ts_code'].nunique()}个代码")

        print(f"    加载 daily 数据 ({load_start} ~ {load_end})...")
        daily_year = _load_year_data_from_dir(STOCK_DAILY_DIR, load_start, load_end, cols=["ts_code", "trade_date", "close"])
        if daily_year.empty:
            print(f"    [WARN] {year} 无 daily 数据")
            continue
        print(f"    daily: {len(daily_year)}行, {daily_year['ts_code'].nunique()}个代码")

        # 合并: stk_limit + daily, on (ts_code, trade_date)
        print("    合并计算...")
        merged = pd.merge(
            stk_limit_year[["ts_code", "trade_date", "down_limit"]],
            daily_year[["ts_code", "trade_date", "close"]],
            on=["ts_code", "trade_date"],
            how="inner",
        )

        # 计算跌停: close <= down_limit
        merged["is_limit_down_close"] = merged["close"] <= merged["down_limit"]

        # 板块标记
        merged["board"] = merged["ts_code"].apply(_get_board)

        # 只保留需要的列
        result = merged[["trade_date", "ts_code", "is_limit_down_close", "board"]].copy()
        result = result.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

        # 增量模式: 与已有年数据合并去重 (ts_code + trade_date)
        if not existing.empty:
            result = pd.concat([existing, result], ignore_index=True)
            result = result.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
            result = result.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

        # 写入
        result.to_parquet(output_path, index=False)

        n_limit_down = result["is_limit_down_close"].sum()
        print(f"    写入: {len(result)}行 | 跌停标记: {n_limit_down}条")
        total_rows += len(result)
        years_done += 1

    print(f"\n  [完成] market_state: {years_done}个年文件 | 累计{total_rows}行")


def _load_year_data_from_dir(directory, start_date, end_date, cols=None):
    """从表目录加载指定区间数据（布局无关）。

    原实现是"逐文件读取 + 内存筛日期"，在 by_code 布局下要扫数千个文件。
    全库已迁移为按年分区，这里改走 common.reader.read_range：
      by_year 布局下只读命中的年份文件，且谓词下推到行组。
    """
    table = os.path.basename(str(directory).rstrip("\\/"))
    return reader.read_range(table, columns=cols,
                             start_date=start_date, end_date=end_date)


# ============================================================
# 辅助函数 (保留旧的 stk_limit 单日函数)
# ============================================================

def update_stk_limit_one_date(pro, limiter, trade_date, dry_run=False):
    """按 trade_date 批量拉全市场 stk_limit，按 ts_code 分组写入。

    ⚠ 已废弃（2026-09-18 补注）: 本函数走 upsert_grouped 写 {code}.parquet，
      而 stk_limit 磁盘已是按年分区。虽当前无调用方（run_stk_limit 已改道
      每日增量引擎），但保留它就是一颗地雷——一旦被调用会在年度目录里
      生成数千小文件并打乱布局探测。
      现已由 common.parquet_store._assert_by_code_target 硬拦（会抛
      LayoutGuardError），双重保险。新代码请走 fetch/daily_update。
    """
    ensure_dir(STK_LIMIT_DIR)
    df = ts_fetch_by_date(pro, "stk_limit", limiter, trade_date)
    if df is None or df.empty:
        print(f"  [WARN] stk_limit trade_date={trade_date} 无数据")
        return (0, 0)

    print(f"  拉取成功: {len(df)}行 | {df['ts_code'].nunique()}个代码")

    if dry_run:
        return (len(df), 0)

    success = 0
    fail = 0
    new_files = 0
    stat = upsert_grouped(
        df, STK_LIMIT_DIR, key_col="ts_code",
        subset="trade_date", sort_by="trade_date",
        on_error=lambda code, e: print(f"  [ERROR] {code}: {e}"),
    )
    success, fail, new_files = stat["processed"], stat["fail"], stat["new"]

    print(f"  完成: 更新={success} (新建={new_files}) 失败={fail}")
    return (success, fail)
