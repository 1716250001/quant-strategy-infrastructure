# -*- coding: utf-8 -*-
"""
tools/check_coverage.py — 可转债与 ETF/LOF 数据覆盖检查
========================================================
重构要点：原实现把全流程写在模块顶层（import 即执行），
现封装为 check_cb() / check_fund() / check_fund_history() / main()，可复用、可测试。

用法:
  python tools/check_coverage.py                # 自动获取最近交易日
  python tools/check_coverage.py 20260817       # 指定日期
  python tools/check_coverage.py --history      # 基金历史起点对账（EE-FUND-1 B1）
  python tools/check_coverage.py --history --strict   # 非零退出码
"""
import argparse
import glob
import os
import sys
from datetime import datetime

import pandas as pd

from common.paths import CB_DAILY_DIR, FUND_DAILY_DIR, META_DIR, MARKET_DATA_DIR
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


# ============================================================
# 三、基金历史起点对账（EE-FUND-1 B1）
# ============================================================
_FUND_HISTORY_TABLES = [
    ("fund_daily",  "trade_date"),
    ("fund_nav",    "nav_date"),
    ("fund_adj",    "trade_date"),
    ("fund_share",  "trade_date"),
]


def _norm_date(s):
    """统一日期格式（fund_basic.list_date 是 YYYYMMDD str 或 NaN）

    支持 str ("20200925"/"2020-09-25") / int (20200925) / float (20200925.0)。
    float 形态来自 pandas 从 CSV 读时的隐式推断（dtype 一变就会触发）。
    """
    if pd.isna(s) or s is None or s == "":
        return None
    # float 形态（如 20200925.0）→ 转 int → str
    try:
        if isinstance(s, float):
            s = int(s)
        s_str = str(s).replace("-", "")
        return datetime.strptime(s_str, "%Y%m%d")
    except (ValueError, TypeError):
        return None


def _table_first_dates(table, date_col, valid_codes):
    """逐分区只读 ts_code+date_col，返回 {ts_code: datetime} —— 每码最早日期。

    高效实现：直接读 parquet 分区文件的两列，groupby 取 min。
    by_year 布局下 ≈0.5s/表（4表合计 ≈2s）。
    """
    result = {}
    d = os.path.join(MARKET_DATA_DIR, table)
    if not os.path.isdir(d):
        return result
    for path in sorted(glob.glob(os.path.join(d, "*.parquet"))):
        try:
            df = pd.read_parquet(path, columns=["ts_code", date_col])
            df = df[df["ts_code"].isin(valid_codes)]
            for tc, grp in df.groupby("ts_code"):
                first = _norm_date(grp[date_col].min())
                if first and (tc not in result or first < result[tc]):
                    result[tc] = first
        except Exception as e:
            print(f"  [WARN] 读 {path} 失败: {e}")
    return result


def check_fund_history(strict=False, return_detail=False, scope="full"):
    """基金历史起点对账（EE-FUND-1 B1）。

    四表（daily/nav/adj/share）× 场内 2,963 只（market='E'），
    逐码比较 first_date vs list_date+30d，输出 A/B 清单与计数。

    A 类 = 库内 0 行（该表完全没有此代码）
    B 类 = 库内有数据但 first_date > list_date + 30 天
    S 类 = 库内 0 行但已在 known_gaps.<table>.source_empty 逐只核验登记的
           「源端确无数据」代码（ts_code 全历史查询空 + 交叉证据），属可接受缺口，
           不触发 strict；注册表与实测必须逐码一致（R17 强校验）。

    Args:
        strict: True 时若存在**未登记**的 A/B 类则返回非零退出码
        return_detail: True 时返回 (rc, detail) tuple（供 R17 断言组消费）
        scope: "full"（默认，四表全跑）/ 表单名 fund_daily|fund_nav|fund_adj|fund_share
               （只跑指定表；EE-FUND-4 起 nav/adj/share 逐表 strict 验收）

    Returns:
        int: 0 表示无未登记缺口（strict=False 时恒 0；strict=True 时有 A/B 返回 1）
        tuple[int, dict]: return_detail=True 时返回 (rc, detail)；
            detail = {table: {"A": [(tc,name)], "B": [(tc,name,gap)],
                              "S": [(tc,name)]}}
    """
    print("=" * 70)
    print(f"三、基金历史起点对账（EE-FUND-1 B1）scope={scope}")
    print("=" * 70)

    # 读「已核验源端空」注册表（§25：逃生口撤销后的唯一豁免通道，逐码登记）
    _kg_path = os.path.join(os.path.dirname(__file__), "known_gaps.json")
    import json as _json
    try:
        with open(_kg_path, encoding="utf-8") as _f:
            _kg = _json.load(_f)
    except Exception:
        _kg = {}

    fund_basic = pd.read_parquet(os.path.join(META_DIR, "fund_basic.parquet"))
    in_fund = fund_basic[fund_basic["market"] == "E"].copy()
    fund_etf_codes = set(in_fund["ts_code"].tolist())
    print(f"\n场内基金（market='E'）: {len(in_fund)} 只")

    # scope 决定跑哪些表
    _table_names = [t for t, _ in _FUND_HISTORY_TABLES]
    if scope == "full":
        tables = list(_FUND_HISTORY_TABLES)
    elif scope in _table_names:
        tables = [(t, c) for t, c in _FUND_HISTORY_TABLES if t == scope]
    else:
        raise ValueError(f"未知 scope={scope!r}，可选: full / {' / '.join(_table_names)}")

    # 预读每表 first_dates
    table_first = {}
    for table, date_col in tables:
        first_map = _table_first_dates(table, date_col, fund_etf_codes)
        table_first[table] = first_map
        print(f"  {table}: 库内 {len(first_map)} 只有效日期")

    # 逐表计算 A/B/S/SB 类
    detail = {}  # {table: {"A":..., "B":..., "S":..., "SB":..., "stale_registered":...}}
    total_A = total_B = total_S = total_SB = 0

    for table, _ in tables:
        first_map = table_first[table]
        registered = set(_kg.get(table, {}).get("source_empty", []))
        registered_late = set(_kg.get(table, {}).get("source_late_start", []))
        A_list, B_list, S_list, SB_list = [], [], [], []

        for _, row in in_fund.iterrows():
            tc = row["ts_code"]
            name = str(row["name"])[:20] if pd.notna(row["name"]) else ""
            list_dt = _norm_date(row["list_date"])

            if tc not in first_map:
                # 已逐只核验登记的源端空 → S 桶；否则是可执行缺口 A
                if tc in registered:
                    S_list.append((tc, name))
                else:
                    A_list.append((tc, name))
                continue

            # 有数据但 list_date 无效无法判定 → 跳过 B 类
            if list_dt is None:
                continue

            gap = (first_map[tc] - list_dt).days
            if gap > 30:
                # 已逐只核验的源端晚起点（分级基金稀疏成交/源端历史残缺）→ SB
                if tc in registered_late:
                    SB_list.append((tc, name, gap))
                else:
                    B_list.append((tc, name, gap))

        # 注册表中但库内实际已无缺口/已有数据的码（注册表失真，须暴露：不能静默）
        stale_reg = sorted(c for c in registered if c in first_map)
        # 晚起点注册表中当前 gap 已 ≤30d 的码（数据修复后注册表须清理）
        stale_late = []
        for tc in sorted(registered_late):
            if tc not in first_map:
                continue  # 0 行归 A/S 逻辑处理，不在此报
            _r = in_fund.loc[in_fund["ts_code"] == tc].iloc[0]
            _ld = _norm_date(_r["list_date"])
            if _ld is None or (first_map[tc] - _ld).days <= 30:
                stale_late.append(tc)
        if stale_reg:
            print(f"\n  ⚠ {table}: source_empty 注册表中有 {len(stale_reg)} 只库内已有数据"
                  f"（注册表须清理）: {stale_reg[:5]}")
        if stale_late:
            print(f"\n  ⚠ {table}: source_late_start 注册表中有 {len(stale_late)} 只 gap 已消失"
                  f"（注册表须清理）: {stale_late[:5]}")

        total_A += len(A_list)
        total_B += len(B_list)
        total_S += len(S_list)
        total_SB += len(SB_list)
        detail[table] = {"A": A_list, "B": B_list, "S": S_list, "SB": SB_list,
                         "stale_registered": stale_reg, "stale_late": stale_late}

        if len(A_list) + len(B_list) + len(S_list) + len(SB_list) == 0:
            continue

        print(f"\n  {table}: A={len(A_list)} B={len(B_list)} "
              f"S={len(S_list)}(源端空) SB={len(SB_list)}(源端晚起点)")
        if A_list:
            print(f"    A 类（库内 0 行·未登记）TOP 10:")
            for tc, nm in A_list[:10]:
                print(f"      {tc:12s} {nm}")
        if B_list:
            B_sorted = sorted(B_list, key=lambda x: -x[2])
            print(f"    B 类（起点晚 >30d·未登记）TOP 10:")
            for tc, nm, gap in B_sorted[:10]:
                print(f"      {tc:12s} {nm[:20]:20s} gap={gap:4d}d")

    print(f"\n===== 汇总（scope={scope}）=====")
    print(f"A 类合计: {total_A}  B 类合计: {total_B}  "
          f"S 类（已登记源端空）: {total_S}  SB 类（已登记源端晚起点）: {total_SB}")

    # 诚实门禁：只看未登记的可执行缺口；注册表失真同样红（由 R17 细查）
    rc = 1 if (strict and (total_A > 0 or total_B > 0)) else 0

    if return_detail:
        return rc, detail
    return rc


def run(date=None, history=False, strict=False, scope="full"):
    """执行覆盖检查（供本文件 CLI 与统一入口 main.py 的 check-coverage 子命令共用）。

    返回退出码（0=健康 / 1=有缺口（strict=True 时））。
    注意：非 history 模式始终返回 0（当日覆盖不影响历史缺口）。
    """
    if history:
        return check_fund_history(strict=strict, scope=scope)
    check_date = date or get_latest_trade_date()
    print(f"检查日期: {check_date}")

    check_cb(check_date)
    check_fund(check_date)
    return 0


def main():
    parser = argparse.ArgumentParser(description="数据覆盖检查")
    parser.add_argument("date", nargs="?", default=None,
                        help="检查日期 YYYYMMDD (默认: 自动获取最近交易日)")
    parser.add_argument("--history", action="store_true",
                        help="基金历史起点对账（EE-FUND-1 B1）")
    parser.add_argument("--strict", action="store_true",
                        help="与 --history 联用：存在 A/B 缺口时非零退出码")
    parser.add_argument("--scope",
                        choices=("full", "fund_daily", "fund_nav", "fund_adj", "fund_share"),
                        default="full",
                        help="历史对账范围：full（四表全跑，默认）/ 单表名")
    args = parser.parse_args()

    rc = run(args.date, history=args.history, strict=args.strict, scope=args.scope)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
