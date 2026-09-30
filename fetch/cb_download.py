# -*- coding: utf-8 -*-
"""
fetch/cb_download.py — 可转债数据下载器
========================================
从原 cb_download.py 重构，复用 fetch/base.py 的共享基建。

从 Tushare 下载全部可转债的基础信息(cb_basic)和历史日线(cb_daily)，
保存为 Parquet 格式，支持断点续跑。

用法:
  python -m fetch.cb_download             # 全量下载（断点续跑）
  python -m fetch.cb_download --reset      # 清除断点, 从头开始
  python -m fetch.cb_download --dry-run     # 空跑, 只统计不下载
"""

import pandas as pd
import os
import time
import argparse
from datetime import datetime, timedelta

# 确保能 import config 和 fetch.base
from config import (
    MARKET_DATA_DIR, META_DIR, CB_DAILY_DIR,
    CB_DAILY_MAX_ROWS, TS_RATE_LIMIT_PER_MIN, FETCH_MAX_WORKERS,
)
from fetch.base import (
    get_pro, RateLimiter, Checkpoint, ensure_dir,
    ts_call_with_retry, fetch_codes_parallel,
)
from common.parquet_store import upsert_by_year
from common.parquet_store import upsert_by_year


# ============================================================
# 可转债专用断点管理 (继承 Checkpoint, 使用独立断点文件)
# ============================================================
class CBCheckpoint(Checkpoint):
    """可转债下载断点管理器，使用独立文件 .cb_checkpoint.json
    继承 base.Checkpoint, 固定 api_name='cb_daily', 省去外部传参"""

    _API_NAME = "cb_daily"

    def __init__(self, data_root=MARKET_DATA_DIR):
        super().__init__(data_root=data_root, filename=".cb_checkpoint.json")

    def is_done(self, ts_code):
        return super().is_done(self._API_NAME, ts_code)

    def mark_done(self, ts_code):
        super().mark_done(self._API_NAME, ts_code)

    def get_completed_count(self):
        return super().get_completed_count(self._API_NAME)


# ============================================================
# 截断续拉 (cb_daily专用, 2000行截断)
# ============================================================
def fetch_cb_daily(pro, limiter, ts_code):
    """拉取单只可转债全历史日线，处理2000行截断"""
    df = ts_call_with_retry(pro, "cb_daily", limiter, {"ts_code": ts_code})
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    # 截断检测
    if len(df) == CB_DAILY_MAX_ROWS:
        earliest = df["trade_date"].min()
        prev_date = (datetime.strptime(earliest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        df_early = ts_call_with_retry(pro, "cb_daily", limiter, {
            "ts_code": ts_code, "end_date": prev_date
        })
        if df_early is not None and not df_early.empty:
            df = pd.concat([df_early, df], ignore_index=True).drop_duplicates()
            print(f"  [截断续拉] {ts_code}: 合并后{len(df)}行")

    return df


# ============================================================
# 主流程
# ============================================================
def run_cb_download(reset=False, dry_run=False, workers=None):
    """执行可转债数据下载。

    供本文件 CLI 与统一入口 main.py 的 download-cb 子命令共用。
    workers: 并发线程数, None=config.FETCH_MAX_WORKERS(=4)
    """
    if workers is None:
        workers = FETCH_MAX_WORKERS

    print("=" * 60)
    print("  可转债数据下载器 (并发版)")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  并发: {workers} 线程 | 限频: 按接口独立")
    print(f"  存储: {os.path.abspath(MARKET_DATA_DIR)}")
    print("=" * 60)

    ensure_dir(MARKET_DATA_DIR)
    limiter = RateLimiter()
    checkpoint = CBCheckpoint(MARKET_DATA_DIR)

    if reset:
        checkpoint.reset()

    pro = get_pro()
    print("[OK] Tushare Pro API 已连接")

    # ── Phase 1: 可转债基础信息 (一次调用) ──
    print("\n" + "=" * 60)
    print("  Phase 1: 可转债基础信息 (cb_basic)")
    print("=" * 60)

    ensure_dir(META_DIR)
    cb_basic_path = os.path.join(META_DIR, "cb_basic.parquet")

    cb_df = ts_call_with_retry(pro, "cb_basic", limiter, {})
    if cb_df is not None and not cb_df.empty:
        cb_df.to_parquet(cb_basic_path, index=False)
        print(f"  可转债总数: {len(cb_df)}")
        print(f"  字段: {len(cb_df.columns)}列")
        print(f"  已保存: {cb_basic_path}")

        if "cb_type" in cb_df.columns:
            print(f"  类型分布: {dict(cb_df['cb_type'].value_counts())}")
        if "delist_date" in cb_df.columns:
            listed = cb_df[cb_df["delist_date"].isna() | (cb_df["delist_date"] == "")]
            delisted = cb_df[cb_df["delist_date"].notna() & (cb_df["delist_date"] != "")]
            print(f"  在市: {len(listed)} | 已摘牌: {len(delisted)}")
    else:
        print("  [ERROR] 未获取到可转债基础信息!")
        return

    # ── Phase 2: 可转债日线行情 (按代码遍历) ──
    print("\n" + "=" * 60)
    print("  Phase 2: 可转债日线行情 (cb_daily)")
    print("=" * 60)

    if not dry_run:
        ensure_dir(CB_DAILY_DIR)

    all_codes = cb_df["ts_code"].tolist()
    total = len(all_codes)
    done = checkpoint.get_completed_count()
    t_start = time.time()

    print(f"  代码数: {total} | 已完成: {done}")

    # 过滤待下载清单: 断点已完成 或 库内已有数据 均跳过
    # ⚠ 全库已改为按年分区，不再能用 "{code}.parquet 是否存在" 判断，
    #   改为用 reader.codes_from_store 取"库内真的有数据"的代码集合。
    pending = []
    skipped = 0
    try:
        from common import reader
        stored = set(reader.codes_from_store("cb_daily"))
    except Exception:
        stored = set()
    for c in all_codes:
        if checkpoint.is_done(c):
            skipped += 1
            continue
        if c in stored:
            if not dry_run:
                checkpoint.mark_done(c)
            skipped += 1
            continue
        pending.append(c)

    if dry_run:
        print(f"  [DRY-RUN] 待下载 {len(pending)} 只, 跳过 {skipped} 只")
    else:
        counters = {"success": 0, "empty": 0, "fail": 0}

        def on_result(ts_code, df, ok):
            """主线程回调: 落盘 + 断点标记 (无并发竞争)

            落盘: cb_daily 已改为按年分区，不能再写 {ts_code}.parquet
                  （旧写法会在年度目录里生成上千个小文件）。
            """
            if ok and df is not None and not df.empty:
                try:
                    upsert_by_year(df, CB_DAILY_DIR, date_col="trade_date",
                                   subset=["ts_code", "trade_date"],
                                   sort_by="trade_date",
                                   on_error=lambda y, e: print(f"  [ERROR] 分区 {y}: {e}"))
                    counters["success"] += 1
                    checkpoint.mark_done(ts_code)
                except Exception as e:
                    print(f"  [ERROR] 保存 {ts_code}: {e}")
                    counters["fail"] += 1
            else:
                if ok:
                    counters["empty"] += 1
                    checkpoint.mark_done(ts_code)
                else:
                    counters["fail"] += 1

            if sum(counters.values()) % 100 == 0:
                checkpoint.save()

        def progress(done_n, tot, stat, elapsed):
            rate = done_n / elapsed * 60 if elapsed > 0 else 0
            eta = (tot - done_n) / rate if rate > 0 else 0
            print(f"  [{done_n}/{tot}] 成功={stat['ok']} 空={stat['empty']} "
                  f"失败={stat['fail']} 跳过={skipped} | "
                  f"速率={rate:.0f}/min | 剩余={eta:.0f}min | 总请求={limiter.total_requests}")

        stat = fetch_codes_parallel(
            pro, "cb_daily", pending, limiter,
            fetch_fn=fetch_cb_daily,
            max_workers=workers,
            on_result=on_result, progress=progress, progress_every=50,
            verbose=False,
        )
        success = stat["ok"]
        empty = stat["empty"]
        fail = stat["fail"]
        checkpoint.save()

        elapsed = time.time() - t_start
        rate = (stat["ok"] + stat["fail"] + stat["empty"]) / elapsed * 60 if elapsed > 0 else 0
        print(f"\n  完成: 成功={success} 空={empty} 失败={fail} 跳过={skipped} | "
              f"耗时={elapsed/60:.1f}min | 稳定速率={rate:.0f}/min")

    # ── 最终报告 ──
    print("\n" + "=" * 60)
    print("  下载完成 — 统计报告")
    print("=" * 60)

    if os.path.exists(cb_basic_path):
        df_meta = pd.read_parquet(cb_basic_path)
        print(f"  cb_basic:            {len(df_meta)}只可转债")

    if os.path.isdir(CB_DAILY_DIR):
        files = [f for f in os.listdir(CB_DAILY_DIR) if f.endswith(".parquet")]
        total_size = sum(os.path.getsize(os.path.join(CB_DAILY_DIR, f)) for f in files)
        print(f"  cb_daily:            {len(files)}文件 | {total_size/1024/1024:.1f}MB | 断点完成={checkpoint.get_completed_count()}")
    else:
        print("  cb_daily:            未下载")

    print(f"\n  总请求: {limiter.total_requests}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="可转债数据下载器（并发版）")
    parser.add_argument("--reset", action="store_true", help="清除断点, 从头开始")
    parser.add_argument("--dry-run", action="store_true", help="空跑, 只统计不下载")
    parser.add_argument("--workers", type=int, default=None,
                        help=f"并发线程数 (默认{FETCH_MAX_WORKERS}, 传1=串行)")
    args = parser.parse_args()

    run_cb_download(reset=args.reset, dry_run=args.dry_run, workers=args.workers)


if __name__ == "__main__":
    main()
