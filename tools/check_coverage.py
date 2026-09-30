# -*- coding: utf-8 -*-
"""
tools/check_coverage.py — 可转债与 ETF/LOF 数据覆盖检查
========================================================
重构要点：原实现把全流程写在模块顶层（import 即执行），
现封装为 check_cb() / check_fund() / main()，可复用、可测试。

用法:
  python tools/check_coverage.py                # 自动获取最近交易日
  python tools/check_coverage.py 20260817       # 指定日期
"""
import argparse
import os

import pandas as pd

from common.paths import CB_DAILY_DIR, FUND_DAILY_DIR, META_DIR
from common import reader
from fetch.base import get_latest_trade_date


def _load_basic(path):
    """读取元数据并把 delist_date 归一为空串"""
    df = pd.read_parquet(path)
    df["delist_date"] = df["delist_date"].fillna("")
    return df


def _scan_with_date(table, check_date):
    """返回 (库内代码全集, 有当日数据的代码集合, 无当日数据的代码集合)

    重构要点: 原实现按文件名逐文件读（by_code 布局）。全库已改为按年分区，
    文件名是年份而不是代码，旧写法会把 "2026" 当成代码。现改走 reader，
    只读命中当日的数据一次，再集合对比。
    """
    codes_all = set(reader.codes_from_store(table))
    day = reader.read_range(table, columns=["ts_code", "trade_date"],
                            start_date=check_date, end_date=check_date)
    has_today = set(day["ts_code"].unique()) if not day.empty else set()
    return codes_all, has_today, codes_all - has_today


# ============================================================
# 一、可转债覆盖分析
# ============================================================
def check_cb(check_date):
    print("=" * 70)
    print("一、可转债覆盖分析")
    print("=" * 70)

    cb_basic = _load_basic(os.path.join(META_DIR, "cb_basic.parquet"))
    print(f"\n1. cb_basic 元数据: {len(cb_basic)} 只可转债（含已退市）")

    active_cb = cb_basic[(cb_basic["delist_date"] == "") | (cb_basic["delist_date"].isna())]
    print(f"   在市（delist_date为空）: {len(active_cb)} 只")
    print(f"   已退市/到期: {len(cb_basic) - len(active_cb)} 只")

    codes_all, has_today, no_today = _scan_with_date("cb_daily", check_date)
    print(f"\n2. cb_daily 库内代码数: {len(codes_all)}")

    active_codes = set(active_cb["ts_code"].tolist())
    with_data = [c for c in has_today if c in active_codes]
    without_data = [c for c in active_codes if c not in has_today]

    print(f"\n3. {check_date} 数据覆盖:")
    print(f"   有当日数据的文件: {len(has_today)}")
    print(f"   无当日数据的文件: {len(no_today)}")
    print("\n4. 在市可转债覆盖率:")
    print(f"   在市 {len(active_cb)} 只中, 有当日数据: {len(with_data)} 只")
    print(f"   在市但缺当日数据: {len(without_data)} 只")

    if without_data:
        missing = active_cb[active_cb["ts_code"].isin(without_data)][
            ["ts_code", "bond_short_name", "list_date", "delist_date", "exchange"]]
        no_file = [c for c in without_data if c not in codes_all]
        has_file = [c for c in without_data if c in codes_all]
        print("\n   其中:")
        print(f"     - 库内无任何数据（未下载）: {len(no_file)} 只")
        print(f"     - 库内有历史数据但无当日（可能停牌）: {len(has_file)} 只")
        if has_file:
            print("\n   有文件但无当日数据的前10只:")
            for _, r in missing[missing["ts_code"].isin(has_file)].head(10).iterrows():
                print(f"     {r['ts_code']:12s} {r['bond_short_name']:10s} "
                      f"list={r['list_date']} exch={r['exchange']}")
        if no_file:
            print("\n   无文件的前10只:")
            for _, r in active_cb[active_cb["ts_code"].isin(no_file)].head(10).iterrows():
                print(f"     {r['ts_code']:12s} {r['bond_short_name']:10s} "
                      f"list={r['list_date']} exch={r['exchange']}")


# ============================================================
# 二、ETF/LOF 覆盖分析
# ============================================================
def check_fund(check_date):
    print("\n" + "=" * 70)
    print("二、ETF/LOF 覆盖分析")
    print("=" * 70)

    fund_basic = _load_basic(os.path.join(META_DIR, "fund_basic.parquet"))
    print(f"\n1. fund_basic 元数据: {len(fund_basic)} 只基金（含已退市）")

    active_fund = fund_basic[(fund_basic["delist_date"] == "") | (fund_basic["delist_date"].isna())]
    print(f"   在市（delist_date为空）: {len(active_fund)} 只")
    print("\n   在市基金类型分布:")
    print(active_fund["fund_type"].value_counts().to_string())

    codes_all, has_today, no_today = _scan_with_date("fund_daily", check_date)
    print(f"\n2. fund_daily 库内代码数: {len(codes_all)}")

    active_codes = set(active_fund["ts_code"].tolist())
    with_data = [c for c in has_today if c in active_codes]
    without_data = [c for c in active_codes if c not in has_today]

    print(f"\n3. {check_date} 数据覆盖:")
    print(f"   有当日数据的文件: {len(has_today)}")
    print(f"   无当日数据的文件: {len(no_today)}")
    print("\n4. 在市基金覆盖率:")
    print(f"   在市 {len(active_fund)} 只中, 有当日数据: {len(with_data)} 只")
    print(f"   在市但缺当日数据: {len(without_data)} 只")

    if without_data:
        no_file = [c for c in without_data if c not in codes_all]
        has_file = [c for c in without_data if c in codes_all]
        print("\n   其中:")
        print(f"     - 库内无任何数据（未下载）: {len(no_file)} 只")
        print(f"     - 库内有历史数据但无当日（可能停牌/新上市）: {len(has_file)} 只")
        if no_file:
            print("\n   无文件的前10只:")
            for _, r in active_fund[active_fund["ts_code"].isin(no_file)].head(10).iterrows():
                print(f"     {r['ts_code']:12s} {r['name']:20s} "
                      f"type={r['fund_type']} list={r['list_date']}")
        if has_file:
            print("\n   有文件但无当日数据的前10只:")
            for _, r in active_fund[active_fund["ts_code"].isin(has_file)].head(10).iterrows():
                print(f"     {r['ts_code']:12s} {r['name']:20s} "
                      f"type={r['fund_type']} list={r['list_date']}")

    # 5. ETF 类型细分
    print("\n5. 在市ETF（基金类型=ETF）覆盖率:")
    active_etf = active_fund[active_fund["fund_type"] == "ETF"]
    print(f"   在市ETF: {len(active_etf)} 只")
    etf_codes = set(active_etf["ts_code"].tolist())
    etf_with_data = [c for c in has_today if c in etf_codes]
    print(f"   有当日数据: {len(etf_with_data)} 只")
    etf_no_data = [c for c in etf_codes if c not in set(has_today)]
    if etf_no_data:
        no_file_etf = [c for c in etf_no_data if c not in codes_all]
        has_file_etf = [c for c in etf_no_data if c in codes_all]
        print(f"   缺当日数据: {len(etf_no_data)} 只")
        print(f"     - 库内无任何数据: {len(no_file_etf)} 只")
        print(f"     - 库内有历史数据但无当日: {len(has_file_etf)} 只")


def run(date=None):
    """执行覆盖检查（供本文件 CLI 与统一入口 main.py 的 check-coverage 子命令共用）。"""
    check_date = date or get_latest_trade_date()
    print(f"检查日期: {check_date}")

    check_cb(check_date)
    check_fund(check_date)


def main():
    parser = argparse.ArgumentParser(description="数据覆盖检查")
    parser.add_argument("date", nargs="?", default=None,
                        help="检查日期 YYYYMMDD (默认: 自动获取最近交易日)")
    args = parser.parse_args()

    run(args.date)


if __name__ == "__main__":
    main()
