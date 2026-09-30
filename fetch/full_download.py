# -*- coding: utf-8 -*-
"""
fetch/full_download.py — 全市场数据下载器（并发版）
====================================================
从原 full_market_download.py 重构，复用 fetch/base.py 的共享基建。

从 Tushare 下载 A股全部个股、上市ETF、上市LOF 的完整历史数据，
本地保存为 Parquet 格式，支持断点续跑。

2000积分: 按接口独立限频(daily=300实测上限, 其余200), 并发4线程/接口
存储格式: Parquet (按API分目录, 按代码分文件)

用法:
  python -m fetch.full_download              # 全量下载（断点续跑）
  python -m fetch.full_download --reset      # 清除断点, 从头开始
  python -m fetch.full_download --phase meta  # 只跑元数据阶段
  python -m fetch.full_download --phase daily # 只跑日线阶段
  python -m fetch.full_download --phase fina  # 只跑财务阶段
  python -m fetch.full_download --dry-run     # 空跑, 只统计不下载
"""

import pandas as pd
import os
import time
import argparse
from datetime import datetime

# 确保能 import config 和 fetch.base
from config import (
    MARKET_DATA_DIR, META_DIR, DOWNLOAD_PHASES,
    TS_RATE_LIMIT_PER_MIN, TS_DAILY_LIMIT, FETCH_MAX_WORKERS,
    TABLE_DATE_COL,
)
from fetch.base import (
    get_pro, RateLimiter, Checkpoint, ensure_dir,
    ts_fetch, fetch_codes_parallel,
)
from common.parquet_store import upsert_by_year


# ============================================================
# 元数据获取
# ============================================================
def fetch_metadata(pro, limiter, data_root):
    """Phase 0: 获取股票/基金代码清单 + 交易日历"""
    print("\n" + "=" * 60)
    print("  Phase 0: 元数据")
    print("=" * 60)

    meta_dir = os.path.join(data_root, "metadata")
    ensure_dir(meta_dir)

    # --- 股票列表 (含退市) ---
    print("\n[0a] 股票列表 (stock_basic)...")
    limiter.wait()
    stock_list = []
    for list_status in ["L", "D", "P"]:  # 上市/退市/暂停上市
        try:
            df = pro.stock_basic(list_status=list_status,
                                 fields="ts_code,symbol,name,area,industry,list_date,delist_date,list_status")
            limiter.record("stock_basic")
            if df is not None and not df.empty:
                stock_list.append(df)
                print(f"  list_status={list_status}: {len(df)}只")
        except Exception as e:
            print(f"  [WARN] stock_basic list_status={list_status}: {e}")

    if stock_list:
        stock_df = pd.concat(stock_list, ignore_index=True).drop_duplicates(subset="ts_code")
        stock_df.to_parquet(os.path.join(meta_dir, "stock_basic.parquet"), index=False)
        print(f"  → 合计 {len(stock_df)} 只股票, 已保存")
    else:
        stock_df = pd.DataFrame()
        print("  [ERROR] 未获取到任何股票数据!")

    # --- 基金列表 (ETF + LOF) ---
    print("\n[0b] 基金列表 (fund_basic)...")
    limiter.wait()
    fund_list = []
    for fund_type in ["ETF", "LOF"]:
        try:
            df = pro.fund_basic(fund_type=fund_type,
                                fields="ts_code,symbol,name,fund_type,list_date,delist_date,list_status")
            limiter.record("fund_basic")
            if df is not None and not df.empty:
                fund_list.append(df)
                print(f"  {fund_type}: {len(df)}只")
        except Exception as e:
            print(f"  [WARN] fund_basic {fund_type}: {e}")

    if fund_list:
        fund_df = pd.concat(fund_list, ignore_index=True).drop_duplicates(subset="ts_code")
        fund_df.to_parquet(os.path.join(meta_dir, "fund_basic.parquet"), index=False)
        print(f"  → 合计 {len(fund_df)} 只基金(ETF+LOF), 已保存")
    else:
        fund_df = pd.DataFrame()
        print("  [ERROR] 未获取到任何基金数据!")

    # --- 交易日历 ---
    print("\n[0c] 交易日历 (trade_cal)...")
    limiter.wait()
    try:
        cal = pro.trade_cal(exchange="SSE", start_date="19900101", end_date="20301231")
        limiter.record("trade_cal")
        if cal is not None and not cal.empty:
            cal.to_parquet(os.path.join(meta_dir, "trade_cal.parquet"), index=False)
            trade_dates = cal[cal["is_open"] == 1]["cal_date"].tolist()
            print(f"  → {len(trade_dates)} 个交易日, 已保存")
        else:
            trade_dates = []
            print("  [WARN] 交易日历为空")
    except Exception as e:
        trade_dates = []
        print(f"  [WARN] trade_cal: {e}")

    return stock_df, fund_df, trade_dates


# ============================================================
# 单API批量下载
# ============================================================
def download_one_api(pro, api_name, codes, limiter, checkpoint, data_root,
                     max_rows=5000, desc="", dry_run=False, workers=None):
    """
    对一组代码逐一调用同一API, 保存为Parquet。

    codes: list of ts_code strings
    workers: 并发线程数, None=config.FETCH_MAX_WORKERS(=4); 传1则串行

    2026-09-15 改造: 由纯串行改为线程池并发(默认4线程)。
    网络请求在 worker 线程并发, 落盘与断点标记在主线程串行(无竞争)。
    """
    if workers is None:
        workers = FETCH_MAX_WORKERS

    api_dir = os.path.join(data_root, api_name)
    if not dry_run:
        ensure_dir(api_dir)

    total = len(codes)
    done_count = checkpoint.get_completed_count(api_name)

    print(f"\n{'─' * 60}")
    print(f"  API: {api_name} ({desc}) | 代码数: {total} | 已完成: {done_count}")
    print(f"  并发: {workers}线程 | 限频: {limiter._rate_for(api_name)}次/分")
    print(f"{'─' * 60}")

    # 过滤待下载清单: 断点已完成 或 目标文件已存在(断点丢失场景) 均跳过
    pending = []
    skipped = 0
    # 过滤待下载清单: 断点已完成 或 库内已有数据 均跳过
    # ⚠ 全库已改为按年分区，不再能用 "{code}.parquet 是否存在" 判断；
    #   改为一次性取出"库内真的有数据"的代码集合（读年度文件的 ts_code 列）。
    try:
        from common import reader
        stored = set(reader.codes_from_store(api_name))
    except Exception:
        stored = set()
    for c in codes:
        if checkpoint.is_done(api_name, c):
            skipped += 1
            continue
        if c in stored:
            # 库内已有数据 → 补记断点, 避免断点丢失后重复拉取
            # (2026-09-15: 实测 fund_daily 断点缺失导致 1,117 次无效重拉)
            if not dry_run:
                checkpoint.mark_done(api_name, c)
            skipped += 1
            continue
        pending.append(c)

    if dry_run:
        print(f"  [DRY-RUN] 待下载 {len(pending)} 只, 跳过 {skipped} 只")
        return

    counters = {"saved": 0, "save_fail": 0}

    def on_result(ts_code, df, ok):
        """主线程回调: 落盘 + 断点标记 (无并发竞争)

        落盘: 默认改为按年分区（by_year）。旧写法写 {ts_code}.parquet 会在
              年度目录里生成数千个小文件，并把 reader 布局探测打回 by_code。
        例外: 无日期列的表（如 stock_company —— 一标的一行的静态元数据，
              全库唯一保留 by_code 的表）仍按标的写单文件。
        """
        if ok and df is not None and not df.empty:
            try:
                date_col = TABLE_DATE_COL.get(api_name, "trade_date")
                if date_col not in df.columns:
                    # 无分区列 → 保持按标的单文件（仅 stock_company）
                    df.to_parquet(os.path.join(api_dir, f"{ts_code}.parquet"),
                                  index=False)
                else:
                    subset = (["ts_code", date_col]
                              if "ts_code" in df.columns else [date_col])
                    _r = upsert_by_year(df, api_dir, date_col=date_col,
                                        subset=subset, sort_by=date_col,
                                        on_error=lambda y, e: print(f"  [ERROR] 分区 {y}: {e}"))
                    # ⚠ 2026-09-22（P0）: fail>0 表示有年份分区没写进去，
                    #   不得记断点（否则该标的数据永不重拉）。抛出后由下方
                    #   except 捕获 → 计入 save_fail 且不标记断点，符合预期。
                    if _r.get("fail"):
                        raise RuntimeError(f"按年分区 {_r['fail']} 个年份写入失败")
                counters["saved"] += 1
                checkpoint.mark_done(api_name, ts_code)
            except Exception as e:
                print(f"  [ERROR] 保存 {ts_code}: {e}")
                counters["save_fail"] += 1
        elif ok and df is not None and df.empty:
            # 该标的确实无数据(如未上市区间), 标记完成避免下次重拉
            checkpoint.mark_done(api_name, ts_code)

        if sum(counters.values()) % 500 == 0 and sum(counters.values()) > 0:
            checkpoint.save()

    def progress(done, tot, stat, elapsed):
        rate = done / elapsed * 60 if elapsed > 0 else 0
        eta_min = (tot - done) / rate if rate > 0 else 0
        print(f"  [{api_name}] {done}/{tot} | "
              f"成功={stat['ok']} 空={stat['empty']} 失败={stat['fail']} 跳过={skipped} | "
              f"速率={rate:.0f}/min | 预估剩余={eta_min:.0f}min | "
              f"{limiter.status_str()}")

    t_start = time.time()
    stat = fetch_codes_parallel(
        pro, api_name, pending, limiter,
        max_workers=workers, max_rows=max_rows,
        on_result=on_result, progress=progress, progress_every=100,
        verbose=False,
    )

    # 最终保存断点
    checkpoint.save()

    elapsed = time.time() - t_start
    rate = (stat["ok"] + stat["fail"] + stat["empty"]) / elapsed * 60 if elapsed > 0 else 0
    print(f"\n  [{api_name}] 完成: 成功={stat['ok']} 空={stat['empty']} 失败={stat['fail']} "
          f"跳过={skipped} | 落盘={counters['saved']} 存盘失败={counters['save_fail']} | "
          f"耗时={elapsed/60:.1f}min | 稳定速率={rate:.0f}/min")


# ============================================================
# 主流程编排
# ============================================================
def run_download(phase="all", reset=False, dry_run=False, workers=None):
    """执行全市场数据下载。

    供本文件 CLI 与统一入口 main.py 的 download-full 子命令共用。
    workers: 并发线程数, None=config.FETCH_MAX_WORKERS(=4)
    """
    if workers is None:
        workers = FETCH_MAX_WORKERS

    print("=" * 60)
    print("  全市场数据下载器 (并发版)")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  并发: {workers} 线程/接口 | 限频: 按接口独立(daily=250, 其余~170)")
    print(f"  日配额: {TS_DAILY_LIMIT}次/天/接口")
    print(f"  存储: {os.path.abspath(MARKET_DATA_DIR)}")
    print(f"  阶段: {phase}")
    print("=" * 60)

    # ── 初始化 ──
    ensure_dir(MARKET_DATA_DIR)
    limiter = RateLimiter()
    checkpoint = Checkpoint(MARKET_DATA_DIR)

    if reset:
        checkpoint.reset()

    # ── Tushare 连接 (复用base.get_pro) ──
    pro = get_pro()
    print("[OK] Tushare Pro API 已连接")

    # ── 元数据 ──
    if phase in ("all", "meta"):
        stock_df, fund_df, trade_dates = fetch_metadata(pro, limiter, MARKET_DATA_DIR)
    else:
        # 非meta阶段, 从本地读取元数据
        stock_df = pd.read_parquet(os.path.join(META_DIR, "stock_basic.parquet")) if \
            os.path.exists(os.path.join(META_DIR, "stock_basic.parquet")) else pd.DataFrame()
        fund_df = pd.read_parquet(os.path.join(META_DIR, "fund_basic.parquet")) if \
            os.path.exists(os.path.join(META_DIR, "fund_basic.parquet")) else pd.DataFrame()
        print(f"[元数据] 从本地加载: 股票={len(stock_df)} 基金={len(fund_df)}")

    # ── 构建代码列表 ──
    stock_codes = stock_df["ts_code"].tolist() if not stock_df.empty else []
    fund_codes = fund_df["ts_code"].tolist() if not fund_df.empty else []

    # 只保留【场内】基金 (SH/SZ) —— 场外 .OF 没有 fund_daily 行情
    #
    # 2026-09-15 修复: 本地 fund_basic.parquet 现为全市场基金表(32,777行，
    # 其中场外 .OF 29,828只)，旧逻辑直接取全表 → 会对 2.9 万只场外基金
    # 发起 fund_daily 请求且全部返回空，实测白白浪费约 89 分钟。
    # 判定优先级: market == "E" > ts_code 后缀属 SH/SZ
    if not fund_df.empty:
        if "market" in fund_df.columns:
            venue = fund_df[fund_df["market"] == "E"]
        else:
            _suffix = fund_df["ts_code"].str.split(".").str[-1]
            venue = fund_df[_suffix.isin(["SH", "SZ"])]

        # 再过滤退市 (字段名在不同版本为 list_status / status)
        for _col in ("list_status", "status"):
            if _col in venue.columns:
                _vals = venue[_col]
                venue = venue[_vals.isin(["L", ""]) | _vals.isna()]
                break

        fund_codes_listed = venue["ts_code"].tolist()
        dropped = len(fund_df) - len(fund_codes_listed)
    else:
        fund_codes_listed = []
        dropped = 0

    print(f"\n[代码清单] 个股: {len(stock_codes)} | 基金(场内SH/SZ): {len(fund_codes_listed)}"
          + (f" | 已剔除场外/退市: {dropped}" if dropped else ""))

    # ── Phase: 日线行情 ──
    if phase in ("all", "daily"):
        for api_conf in DOWNLOAD_PHASES["daily"]["apis"]:
            api_name = api_conf["api"]
            desc = api_conf["desc"]
            max_rows = api_conf["max_rows"]
            codes = stock_codes if api_name in ("daily", "daily_basic") else fund_codes_listed
            download_one_api(pro, api_name, codes, limiter, checkpoint,
                             MARKET_DATA_DIR, max_rows=max_rows, desc=desc,
                             dry_run=dry_run, workers=workers)

    # ── Phase: 财务数据 ──
    if phase in ("all", "fina"):
        for api_conf in DOWNLOAD_PHASES["fina"]["apis"]:
            api_name = api_conf["api"]
            desc = api_conf["desc"]
            max_rows = api_conf["max_rows"]
            download_one_api(pro, api_name, stock_codes, limiter, checkpoint,
                             MARKET_DATA_DIR, max_rows=max_rows, desc=desc,
                             dry_run=dry_run, workers=workers)

    # ── 最终报告 ──
    print("\n" + "=" * 60)
    print("  下载完成 — 统计报告")
    print("=" * 60)

    for api_name in ["daily", "daily_basic", "fund_daily",
                     "income", "balancesheet", "cashflow", "fina_indicator"]:
        api_dir = os.path.join(MARKET_DATA_DIR, api_name)
        if os.path.isdir(api_dir):
            files = [f for f in os.listdir(api_dir) if f.endswith(".parquet")]
            total_size = sum(os.path.getsize(os.path.join(api_dir, f)) for f in files)
            done = checkpoint.get_completed_count(api_name)
            print(f"  {api_name:20s}: {len(files):5d}文件 | "
                  f"{total_size/1024/1024:8.1f}MB | "
                  f"断点完成={done}")
        else:
            print(f"  {api_name:20s}: 未下载")

    if os.path.isdir(META_DIR):
        meta_files = os.listdir(META_DIR)
        print(f"  {'metadata':20s}: {len(meta_files)}文件")
    else:
        print(f"  {'metadata':20s}: 未下载")

    print(f"\n  总请求: {limiter.total_requests}")
    print(f"  频次统计: {limiter.status_str()}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="全市场数据下载器（并发版）")
    parser.add_argument("--reset", action="store_true", help="清除断点, 从头开始")
    parser.add_argument("--phase", type=str, default="all",
                        help="指定阶段: meta / daily / fina / all")
    parser.add_argument("--dry-run", action="store_true", help="空跑模式, 只统计不下载")
    parser.add_argument("--workers", type=int, default=None,
                        help=f"并发线程数 (默认{FETCH_MAX_WORKERS}, 传1=串行)")
    args = parser.parse_args()

    run_download(phase=args.phase, reset=args.reset, dry_run=args.dry_run,
                 workers=args.workers)


if __name__ == "__main__":
    main()
