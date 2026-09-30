# -*- coding: utf-8 -*-
"""
fetch/intraday_update.py — 尾盘全量增量更新（**已废弃**）
============================================================
⛔ 2026-09-17 起本模块不再作为每日更新入口。原因:

   本模块按 trade_date 拉全市场当日数据，然后走 upsert_grouped
   **按 ts_code 分组逐文件读写**。全库已统一为按年分区（by_year），
   该落盘方式会在年度目录里凭空生成数千个小文件，并把 reader 的布局探测
   打回 by_code，导致下游读数全面降级。

   替代: **fetch/daily_update.py**（同一个 by_date 拉取路径，但落盘改为
         YearBatchWriter 批量写年度文件，并带断点三态与复核窗口）

   CLI 入口保持不变:  python main.py intraday-update
                      （main.py 已转发到 fetch/daily_update.py）

   本文件仅保留供代码考古/参照，**不要直接调用**。

⚠ **为什么不直接删除本文件（2026-09-19 代码审查结论）**:
   回归测试 **R7** 会 import 本模块并断言 `run_update()` 抛
   DeprecatedEngineError —— 这是"废弃引擎必须被硬拦"的**正向验证**。
   删除文件会让 R7 失去验证对象。本文件属**受保护的死代码**，请勿清理；
   若确需移除，必须同步删除 R7 对应用例。
"""

import os
import sys
import time
import argparse
from datetime import datetime

import pandas as pd

from config import (
    INTRADAY_UPDATE_APIS, INTRADAY_RATE_INTERVAL,
    MARKET_DATA_DIR, PAGED_APIS,
)
from common.paths import ensure_dir
from common.parquet_store import upsert_grouped
from fetch.base import (
    get_pro, get_latest_trade_date,
    RateLimiter, ts_fetch_by_date, ts_call_with_retry,
)


# ============================================================
# 分页拉取 (针对index_daily等大返回量的API)
# ============================================================

def _fetch_by_date_paged(pro, api_name, limiter, trade_date, page_size=4500):
    """
    按trade_date分页拉取数据。
    Tushare单次返回上限5000行，index_daily全市场约10000+个指数，
    需用offset分页拉取。
    """
    all_chunks = []
    offset = 0
    while True:
        params = {"trade_date": trade_date, "limit": page_size, "offset": offset}
        df = ts_call_with_retry(pro, api_name, limiter, params)
        if df is None or df.empty:
            break
        all_chunks.append(df)
        if len(df) < page_size:
            break
        offset += page_size
    if not all_chunks:
        return None
    return pd.concat(all_chunks, ignore_index=True)


# ============================================================
# 单API增量更新
# ============================================================
def update_one_api(pro, api_name, limiter, trade_date, dry_run=False):
    """
    对一个API按trade_date批量拉取当日全市场数据，
    按ts_code分组追加到各Parquet文件。

    参数:
        api_name: Tushare API名 (如 "daily")
        trade_date: YYYYMMDD格式
        dry_run: 空跑模式

    返回:
        (success_count, fail_count)
    """
    dir_path, key_col, date_col = INTRADAY_UPDATE_APIS[api_name]

    print(f"\n{'─' * 60}")
    print(f"  API: {api_name} | 日期: {trade_date} | 目录: {os.path.basename(dir_path)}")
    print(f"{'─' * 60}")

    if not dry_run:
        ensure_dir(dir_path)

    # 分页拉取 (针对index_daily等返回行数可能超过5000的API)
    if api_name in PAGED_APIS:
        df = _fetch_by_date_paged(pro, api_name, limiter, trade_date)
    else:
        df = ts_fetch_by_date(pro, api_name, limiter, trade_date)

    if df is None or df.empty:
        print(f"  [WARN] {api_name} trade_date={trade_date} 无数据")
        return (0, 0)

    print(f"  拉取成功: {len(df)}行 | {df[key_col].nunique()}个代码")

    if dry_run:
        print("  [DRY-RUN] 跳过写入")
        return (len(df), 0)

    # 按 ts_code 分组，合并追加到各文件（统一走 common.parquet_store）
    t_start = time.time()
    stat = upsert_grouped(
        df, dir_path, key_col=key_col, subset=date_col, sort_by=date_col,
    )
    success = stat["processed"]
    fail = stat["fail"]
    new_files = stat["new"]

    elapsed = time.time() - t_start
    print(f"  完成: 更新={success} (新建={new_files}) 失败={fail} | 耗时={elapsed:.1f}s")
    return (success, fail)


# ============================================================
# 废弃守卫
# ============================================================
class DeprecatedEngineError(RuntimeError):
    """旧每日更新引擎已被停用（会破坏按年分区布局）"""


def _guard_deprecated():
    """禁止旧引擎被直接调用（原因见模块 docstring 与 gap_update 的同名守卫）。

    本模块的落盘走 upsert_grouped（按 ts_code 逐文件），在全库改为按年分区后
    会在年度目录里生成数千个小文件，并把 reader 的布局探测打回 by_code，
    导致下游读数全面降级且不报错。故硬拦。
    """
    raise DeprecatedEngineError(
        f"\n{'=' * 74}\n"
        f"  ⛔ fetch/intraday_update.py 已废弃，不可直接调用。\n"
        f"{'=' * 74}\n"
        f"  原因: 按 ts_code 逐文件落盘，与当前的全库【按年分区】布局冲突，\n"
        f"        会在年度目录里生成数千个小文件并打乱 reader 的布局探测。\n"
        f"\n"
        f"  请改用（命令名不变）:\n"
        f"        python main.py intraday-update       # 补缺口 + 复核最近 1 个交易日\n"
        f"        python main.py gap-update            # 每日增量更新（默认复核 3 日）\n"
        f"        python -m fetch.daily_update --help  # 引擎本体\n"
        f"{'=' * 74}\n")


# ============================================================
# 主流程
# ============================================================
def run_update(trade_date=None, dry_run=False):
    """
    执行尾盘增量更新全流程（**已停用**，调用即抛 DeprecatedEngineError）。

    参数:
        trade_date: YYYYMMDD格式，None=自动获取最近交易日
        dry_run: 空跑模式
    """
    _guard_deprecated()
    # 获取交易日
    if trade_date is None:
        trade_date = get_latest_trade_date()
    else:
        trade_date = get_latest_trade_date(trade_date)

    print("=" * 60)
    print("  尾盘增量更新 (全市场当日数据)")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  交易日: {trade_date}")
    print(f"  请求间隔: {INTRADAY_RATE_INTERVAL}秒")
    print(f"  涉及API: {list(INTRADAY_UPDATE_APIS.keys())}")
    print(f"  存储: {os.path.abspath(MARKET_DATA_DIR)}")
    if dry_run:
        print("  模式: DRY-RUN (只统计不写入)")
    print("=" * 60)

    pro = get_pro()
    print("[OK] Tushare Pro API 已连接")

    # 使用独立的限流器（更保守的间隔）
    limiter = RateLimiter(
        rate_per_min=int(60.0 / INTRADAY_RATE_INTERVAL),
        daily_limit=90000,
    )

    total_success = 0
    total_fail = 0

    for api_name in INTRADAY_UPDATE_APIS:
        s, f = update_one_api(pro, api_name, limiter, trade_date, dry_run=dry_run)
        total_success += s
        total_fail += f

    # 最终报告
    print(f"\n{'=' * 60}")
    print("  尾盘增量更新完成 — 统计报告")
    print(f"{'=' * 60}")
    print(f"  交易日: {trade_date}")
    print(f"  总更新: {total_success} | 总失败: {total_fail}")
    print(f"  总请求: {limiter.total_requests}")
    print(f"  频次统计: {limiter.status_str()}")
    print(f"{'=' * 60}")

    return total_success, total_fail


# ============================================================
# CLI入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="尾盘增量更新 (已废弃, 请用 main.py intraday-update)")
    parser.add_argument("--date", default=None,
                        help="目标日期 YYYYMMDD (默认: 自动获取最近交易日)")
    parser.add_argument("--dry-run", action="store_true", help="空跑模式, 只统计不写入")
    args = parser.parse_args()

    try:
        run_update(trade_date=args.date, dry_run=args.dry_run)
    except DeprecatedEngineError as e:
        print(str(e))
        sys.exit(2)


if __name__ == "__main__":
    main()
