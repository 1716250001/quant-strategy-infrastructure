# -*- coding: utf-8 -*-
"""
fetch/gap_update.py — 差额补全更新（**已废弃**）
================================================
⛔ 2026-09-17 起本模块不再作为每日更新入口。原因:

   1. 全库已统一为**按年分区**（by_year）布局，而本模块落盘走
      upsert_grouped（按 ts_code 逐文件读写），会在年度目录里凭空生成
      数千个小文件，并把 reader 的布局探测打回 by_code，
      导致下游（背离扫描 / 奇点扫描 / 体检）读数全面降级。
   2. 崩溃成本高一个量级: daily 5,903 文件 × read→concat→write
      ≈ 111 秒/天，index_daily ≈ 200 秒/天；
      改为按年分区后每天只改 1 个年度文件，毫秒级。

   替代: **fetch/daily_update.py**（按日并发 + YearBatchWriter 批量落盘 +
         断点三态纪律 + 复核窗口）

   CLI 入口保持不变:  python main.py gap-update
                      （main.py 已转发到 fetch/daily_update.py）

   本文件仅保留供代码考古/参照，**不要在流水线与定时任务中直接调用**
   （python -m fetch.gap_update 仍会执行旧逻辑，会污染目录布局）。

⚠ **为什么不直接删除本文件（2026-09-19 代码审查结论）**:
   回归测试 **R7**（tools/regress_pipeline.py）会 import 本模块并断言
   `run_gap_update()` 抛 DeprecatedEngineError —— 这是对"废弃引擎必须被硬拦"
   的**正向验证**。若删除文件，R7 将失去验证对象（import 直接失败），
   安全断言名存实亡。
   因此本文件是**受保护的死代码**：体积换安全，请勿清理。
   若确需移除，必须同步删除 R7 对应用例并说明理由。
"""

import os
import sys
import time
import argparse
from datetime import datetime

import pandas as pd

from config import (
    INTRADAY_UPDATE_APIS, INTRADAY_RATE_INTERVAL,
    GAP_UPDATE_SAMPLE_CODES,
)
from common.calendar import trade_dates_between
from fetch.base import (
    get_pro, get_latest_trade_date,
    RateLimiter,
)
from fetch.intraday_update import update_one_api


# ============================================================
# 数据库日期扫描
# ============================================================

# 采样标的从 config.py 读取（按目录类型分开配置）
SAMPLE_TS_CODES = GAP_UPDATE_SAMPLE_CODES


def scan_db_latest_date():
    """
    扫描各数据目录，返回每个目录的最新日期。

    策略：对每个目录，逐个尝试采样标的，取第一个有数据的文件的最大日期。
    取各目录中最小值作为数据库整体最新日期（保守策略）。

    返回:
        (db_latest_date: str, detail: dict)
        db_latest_date = 各目录最新日期中的最小值 (YYYYMMDD)
        detail = {api_name: latest_date}
    """
    detail = {}

    for api_name, (dir_path, key_col, date_col) in INTRADAY_UPDATE_APIS.items():
        found = False
        # 按目录类型获取对应采样标的
        sample_codes = SAMPLE_TS_CODES.get(api_name, [])
        for ts_code in sample_codes:
            fpath = os.path.join(dir_path, f"{ts_code}.parquet")
            if os.path.exists(fpath):
                try:
                    df = pd.read_parquet(fpath, columns=[date_col])
                    if not df.empty:
                        latest = str(df[date_col].max())
                        detail[api_name] = latest
                        found = True
                        break
                except Exception as e:
                    print(f"  [WARN] 读取采样文件失败 {fpath}: {str(e)[:60]}")
                    continue

        if not found:
            # 采样标的都没有，尝试目录中第一个文件
            if os.path.isdir(dir_path):
                files = [f for f in os.listdir(dir_path) if f.endswith(".parquet")]
                if files:
                    try:
                        fpath = os.path.join(dir_path, files[0])
                        df = pd.read_parquet(fpath, columns=[date_col])
                        if not df.empty:
                            latest = str(df[date_col].max())
                            detail[api_name] = latest
                            found = True
                    except Exception as e:
                        print(f"  [WARN] 读取目录首个文件失败 {fpath}: {str(e)[:60]}")

        if not found:
            detail[api_name] = None

    # 取各目录最新日期中的最小值（最保守的目录决定补全起点）
    valid_dates = [d for d in detail.values() if d is not None]
    if valid_dates:
        db_latest = min(valid_dates)
    else:
        db_latest = None

    return db_latest, detail


# ============================================================
# 获取交易日历中间隔
# ============================================================

def get_trade_dates_between(start_date, end_date):
    """
    获取 start_date（不含）到 end_date（含）之间的所有交易日。

    参数:
        start_date: YYYYMMDD (数据库最新日期，不含)
        end_date: YYYYMMDD (目标日期，含)

    返回:
        list of YYYYMMDD strings, 按日期升序（日历缺失时返回空列表）

    实现已统一至 common.calendar.trade_dates_between。
    """
    try:
        return trade_dates_between(start_date, end_date)
    except FileNotFoundError as e:
        print(f"  [ERROR] {e}，请先运行全量下载")
        return []


# ============================================================
# 主流程
# ============================================================

# ============================================================
# 废弃守卫
# ============================================================
class DeprecatedEngineError(RuntimeError):
    """旧每日更新引擎已被停用（会破坏按年分区布局且误判缺口）"""


def _guard_deprecated(entry):
    """禁止旧引擎被直接调用。

    为什么加硬拦而不是只写注释:
      旧实现用采样标的拼 "{ts_code}.parquet" 探测最新日期。全库改为按年分区后
      该路径取不到文件，会退化到"取目录中第一个文件" —— 那是**最早的年份**，
      于是把"库内最新日期"误判成 1990 年，进而认为有 30+ 年缺口、
      发起数千次无效请求（实测回归中显示 最新=19901225）。
      同时它的落盘方式会在年度目录里生成数千个小文件。
      静默跑错的代价远大于直接报错，故硬拦。
    """
    raise DeprecatedEngineError(
        f"\n{'=' * 74}\n"
        f"  ⛔ fetch/{entry} 已废弃，不可直接调用。\n"
        f"{'=' * 74}\n"
        f"  原因: 该模块按 ts_code 逐文件读写，与当前的全库【按年分区】布局冲突:\n"
        f"        1) 探测最新日期会误判为 1990 年（取到了最早的年份文件）\n"
        f"        2) 落盘会在年度目录里生成数千个小文件，打乱布局\n"
        f"\n"
        f"  请改用（命令名不变，功能等价且带复核窗口）:\n"
        f"        python main.py gap-update            # 每日增量更新\n"
        f"        python main.py intraday-update       # 只关心最近 1 个交易日\n"
        f"        python -m fetch.daily_update --help  # 引擎本体\n"
        f"\n"
        f"  若确需查看旧实现，请直接阅读源码，不要执行本模块。\n"
        f"{'=' * 74}\n")


# ============================================================
# 主流程
# ============================================================
def run_gap_update(target_date=None, dry_run=False):
    """
    执行差额补全更新（**已停用**，调用即抛 DeprecatedEngineError）。

    参数:
        target_date: YYYYMMDD格式，None=自动获取最近交易日
        dry_run: 空跑模式
    """
    _guard_deprecated("gap_update.py")
    # ============================================================
    # Step 1: 扫描各目录最新日期
    # ============================================================
    print("=" * 60)
    print("  差额补全更新")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    db_latest, detail = scan_db_latest_date()

    print("\n[Step 1] 数据库日期扫描:")
    for api_name, dt in detail.items():
        dir_name = INTRADAY_UPDATE_APIS[api_name][0]
        dir_name = os.path.basename(dir_name)
        print(f"  {api_name:15s} ({dir_name:15s}): {dt or '无数据'}")

    if db_latest is None:
        print("\n  [ERROR] 无法确定数据库最新日期，请先运行全量下载")
        return 0, 0

    # ============================================================
    # Step 2: 确定目标日期
    # ============================================================
    if target_date is None:
        target_date = get_latest_trade_date()
    else:
        target_date = get_latest_trade_date(target_date)

    print(f"\n[Step 2] 目标日期: {target_date}")

    # ============================================================
    # Step 3: 按目录独立计算缺口
    # ============================================================
    print("\n[Step 3] 缺口分析 (按目录独立):")

    api_gaps = {}  # {api_name: [missing_dates]}
    total_missing = 0
    total_api_calls = 0

    for api_name in INTRADAY_UPDATE_APIS:
        api_latest = detail.get(api_name)
        if api_latest is None:
            print(f"  {api_name:15s}: 无数据，跳过")
            continue

        if target_date <= api_latest:
            print(f"  {api_name:15s}: 已是最新 ({api_latest} >= {target_date})，无需补全")
            api_gaps[api_name] = []
            continue

        gaps = get_trade_dates_between(api_latest, target_date)
        api_gaps[api_name] = gaps
        total_missing += len(gaps)
        total_api_calls += len(gaps)
        dir_name = os.path.basename(INTRADAY_UPDATE_APIS[api_name][0])
        print(f"  {api_name:15s} ({dir_name:15s}): {api_latest} → {target_date}, "
              f"缺口 {len(gaps)} 天")

        if gaps and len(gaps) <= 30:
            print(f"    缺口日期: {', '.join(gaps)}")
        elif gaps:
            print(f"    缺口范围: {gaps[0]} ~ {gaps[-1]} (共{len(gaps)}天)")

    if total_missing == 0:
        print("\n  所有目录均已是最新，无需补全")
        return 0, 0

    print(f"\n  总缺口: {total_missing} 个API-交易日")
    print(f"  总API调用: {total_api_calls} 次")

    if dry_run:
        print("\n  [DRY-RUN] 只统计不写入")
        print(f"  预估耗时: {total_api_calls * INTRADAY_RATE_INTERVAL / 60:.0f} 分钟")
        return 0, 0

    # ============================================================
    # Step 4: 逐日逐API补全
    # ============================================================
    print("\n[Step 4] 开始逐日补全:")
    print(f"  请求间隔: {INTRADAY_RATE_INTERVAL}秒")

    pro = get_pro()
    print("[OK] Tushare Pro API 已连接")

    limiter = RateLimiter(
        rate_per_min=int(60.0 / INTRADAY_RATE_INTERVAL),
        daily_limit=90000,
    )

    grand_success = 0
    grand_fail = 0
    t_start = time.time()
    processed = 0

    for api_name, gaps in api_gaps.items():
        if not gaps:
            continue

        dir_name = os.path.basename(INTRADAY_UPDATE_APIS[api_name][0])
        print(f"\n{'━' * 60}")
        print(f"  API: {api_name} ({dir_name}) | 需补全 {len(gaps)} 天")
        print(f"{'━' * 60}")

        api_success = 0
        api_fail = 0

        for i, dt in enumerate(gaps):
            print(f"\n  [{api_name}] 进度: {i+1}/{len(gaps)} | 日期: {dt}")

            s, f = update_one_api(pro, api_name, limiter, dt, dry_run=False)
            api_success += s
            api_fail += f
            processed += 1

            elapsed = time.time() - t_start
            print(f"  当日完成: 更新={s} 失败={f} | "
                  f"累计耗时={elapsed/60:.1f}min")

        grand_success += api_success
        grand_fail += api_fail
        print(f"\n  {api_name} 完成: 更新={api_success} 失败={api_fail}")

    # ============================================================
    # Step 5: 最终报告
    # ============================================================
    elapsed = time.time() - t_start
    print(f"\n{'=' * 60}")
    print("  差额补全完成 — 统计报告")
    print(f"{'=' * 60}")
    print(f"  目标日期:           {target_date}")
    for api_name in INTRADAY_UPDATE_APIS:
        api_latest = detail.get(api_name, '?')
        gaps = api_gaps.get(api_name, [])
        dir_name = os.path.basename(INTRADAY_UPDATE_APIS[api_name][0])
        print(f"  {api_name:15s} ({dir_name:15s}): {api_latest} → {target_date} "
              f"({len(gaps)}天)")
    print(f"  总更新: {grand_success} | 总失败: {grand_fail}")
    print(f"  总请求: {limiter.total_requests}")
    print(f"  频次统计: {limiter.status_str()}")
    print(f"  总耗时: {elapsed/60:.1f} 分钟")
    print(f"{'=' * 60}")

    return grand_success, grand_fail


# ============================================================
# CLI入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="差额补全更新 (已废弃, 请用 main.py gap-update)")
    parser.add_argument("--date", default=None,
                        help="目标日期 YYYYMMDD (默认: 自动获取最近交易日)")
    parser.add_argument("--dry-run", action="store_true", help="空跑模式, 只统计不写入")
    args = parser.parse_args()

    try:
        run_gap_update(target_date=args.date, dry_run=args.dry_run)
    except DeprecatedEngineError as e:
        print(str(e))
        sys.exit(2)


if __name__ == "__main__":
    main()
