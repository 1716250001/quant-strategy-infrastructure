# -*- coding: utf-8 -*-
"""
tools/quicklook.py — 数据速查（freshness / peek）
==================================================
回答日常最常问的两个问题，避免为了看一眼数据而跑 4 分钟的 db-audit：

  freshness  各表最新日期一览（秒级）
  peek       看某张表/某只标的的实际数据长什么样

设计要点:
  - **不读全量数据**：freshness 只用 reader.latest_date（by_year 下只读最后一个
    年度文件的日期列），peek 默认只取尾部若干行
  - 输出与 db-audit/结构报告口径一致（同用 reader，布局无关）

用法:
  python main.py freshness                    # 核心表一览
  python main.py freshness --all              # 全部表
  python main.py freshness daily,moneyflow    # 指定表
  python main.py freshness --stale 3          # 只列落后超过N个交易日的

  python main.py peek daily --code 600519.SH --rows 10
  python main.py peek margin --date 20260918
  python main.py peek fund_basic --rows 5
"""
import argparse
import os
import sys
from datetime import datetime

from config import MARKET_DATA_DIR, BACKFILL_TARGETS, DAILY_UPDATE_EXTRA
from common import reader
from common.calendar import open_dates


# 核心表（日常最常看）
CORE_TABLES = [
    "daily", "daily_basic", "fund_daily", "cb_daily", "index_daily",
    "adj_factor", "stk_limit", "moneyflow", "margin", "margin_detail",
    "hk_hold", "fut_daily", "fut_mapping", "fut_settle",
    "top_list", "top_inst", "block_trade", "suspend_d",
]

# 各表日期列（优先取 config 权威定义，兜底用候选）
_DATE_FALLBACK = ("trade_date", "cal_date", "ann_date", "date", "end_date",
                  "nav_date", "start_date")


def _date_col_for(table):
    from config import TABLE_DATE_COL
    return TABLE_DATE_COL.get(table)


def _all_tables():
    """库内所有有 parquet 的目录（排除元数据/派生目录）"""
    out = []
    if not os.path.isdir(MARKET_DATA_DIR):
        return out
    for e in sorted(os.scandir(MARKET_DATA_DIR), key=lambda x: x.name):
        if not e.is_dir() or e.name.startswith("."):
            continue
        if e.name in ("metadata", "custom_index"):
            continue
        if any(f.endswith(".parquet") for f in os.listdir(e.path)):
            out.append(e.name)
    return out


def _latest(table):
    """该表最新日期（布局无关；by_code/single 用兜底扫描）"""
    dc = _date_col_for(table)
    reader.clear_cache(table)
    try:
        d = reader.latest_date(table, date_col=dc) if dc else reader.latest_date(table)
        if d:
            return d
    except Exception:
        pass
    # 兜底：按候选列探测
    dd = os.path.join(MARKET_DATA_DIR, table)
    files = sorted(f for f in os.listdir(dd) if f.endswith(".parquet"))
    if not files:
        return None
    try:
        import pyarrow.parquet as pq
        import pandas as pd
        names = list(pq.ParquetFile(os.path.join(dd, files[-1])).schema_arrow.names)
        col = next((c for c in ([dc] if dc else []) + list(_DATE_FALLBACK)
                    if c in names), None)
        if not col:
            return None
        s = pd.read_parquet(os.path.join(dd, files[-1]), columns=[col])[col]
        return str(s.max()) if len(s) else None
    except Exception:
        return None


# ============================================================
# freshness
# ============================================================
def run_freshness(tables=None, show_all=False, stale_days=None, verbose=True):
    """各表最新日期一览。

    stale_days: 只显示落后超过 N 个交易日的表（None=全显示）
    """
    names = tables or (_all_tables() if show_all else list(CORE_TABLES))
    names = [n for n in names if os.path.isdir(os.path.join(MARKET_DATA_DIR, n))]
    if not names:
        print("  无可查的表")
        return []

    # 基准日 = 最近已收盘交易日
    # ⚠ 两个易错点（2026-09-19 代码审查修复）:
    #   ① 起点不可写死年份。原实现写 start_date="20260101"，跨年后若交易日历
    #      尚未含当年数据，ds 会退化到上一年末 → 全表误报"落后 N 天"。
    #      改为按 today 动态回溯两年（一年至少 240 个交易日，必然覆盖）。
    #   ② 盘中（15:05 前）今日尚未收盘，T 日数据源必然未发布。若仍把今天当
    #      基准日，全表都会显示"差1天" —— 每天盘中固定出现的假报警
    #      （"狼来了"效应，真落后反而被淹没）。
    from datetime import time as _time
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    ds = [d for d in open_dates(start_date=f"{int(today[:4]) - 1}0101")
          if d <= today]
    if ds and ds[-1] == today and now.time() < _time(15, 5):
        ds = ds[:-1]
    base = ds[-1] if ds else today

    rows = []
    for n in names:
        dt = _latest(n)
        if dt is None:
            rows.append((n, None, None))
            continue
        lag = len([d for d in ds if str(dt) < d <= base])
        rows.append((n, dt, lag))

    if stale_days is not None:
        rows = [x for x in rows if x[2] is None or x[2] >= stale_days]

    if verbose:
        print("=" * 88)
        print(f"  数据新鲜度（基准交易日 {base}）")
        print("=" * 88)
        print(f"  {'表名':18s} {'最新日期':>10s} {'落后':>6s}  状态")
        print("-" * 88)
        for n, dt, lag in rows:
            if dt is None:
                print(f"  {n:18s} {'无数据':>10s} {'—':>6s}")
                continue
            if lag == 0:
                st = "✓ 最新"
            elif lag == 1:
                st = "· 差1天（T日数据源可能未发布）"
            elif lag <= 3:
                st = f"· 差{lag}天"
            else:
                st = f"⚠ 差{lag}天（偏旧）"
            print(f"  {n:18s} {str(dt):>10s} {lag:6d}  {st}")
        print("-" * 88)
        n_stale = sum(1 for _, d, l in rows if l is not None and l > 3)
        print(f"  共 {len(rows)} 张表" +
              (f"，其中 {n_stale} 张偏旧（>3 个交易日）" if n_stale else "，全部新鲜"))
    return rows


# ============================================================
# peek
# ============================================================
def run_peek(table, code=None, date=None, rows=10, cols=None, verbose=True):
    """查看某张表的实际数据（默认尾部 N 行）"""
    d = os.path.join(MARKET_DATA_DIR, table)
    if not os.path.isdir(d):
        print(f"  ✗ 表不存在: {table}")
        return None

    dc = _date_col_for(table) or "trade_date"

    if code:
        df = reader.read_code(table, code, root=MARKET_DATA_DIR)
    elif date:
        # 按日期取全市场（读年度文件 + 过滤）
        df = reader.read_range(table, start_date=date, end_date=date,
                               date_col=dc, root=MARKET_DATA_DIR)
    else:
        # 无筛选：只读最新一个年度文件（避免全量）
        files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
        if not files:
            print(f"  ✗ 表为空: {table}")
            return None
        import pandas as pd
        df = pd.read_parquet(os.path.join(d, files[-1]))

    if df is None or df.empty:
        print(f"  （无匹配数据）table={table} code={code} date={date}")
        return df

    if cols:
        keep = [c for c in cols.split(",") if c.strip() in df.columns]
        if keep:
            df = df[keep]

    if verbose:
        print("=" * 88)
        print(f"  数据预览: {table}"
              + (f" | code={code}" if code else "")
              + (f" | date={date}" if date else ""))
        print("=" * 88)
        print(f"  行数={len(df):,}  列数={len(df.columns)}")
        print(f"  列: {list(df.columns)}")
        print("-" * 88)
        n = min(int(rows or 10), len(df))
        with_pd = __import__("pandas")
        with_pd.set_option("display.width", 200)
        with_pd.set_option("display.max_columns", 40)
        print(df.tail(n).to_string(index=False))
        print("-" * 88)
        if dc in df.columns:
            print(f"  {dc} 范围: {df[dc].min()} ~ {df[dc].max()}")
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description="数据速查（freshness / peek）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("freshness", help="各表最新日期一览")
    p.add_argument("tables", nargs="?", default=None,
                   help="逗号分隔的表名（默认核心表）")
    p.add_argument("--all", action="store_true", help="显示全部表")
    p.add_argument("--stale", type=int, default=None,
                   help="只显示落后超过N个交易日的表")

    p = sub.add_parser("peek", help="查看表数据")
    p.add_argument("table", help="表名")
    p.add_argument("--code", default=None, help="标的代码")
    p.add_argument("--date", default=None, help="日期 YYYYMMDD")
    p.add_argument("--rows", type=int, default=10, help="显示行数（默认10）")
    p.add_argument("--cols", default=None, help="只显示指定列（逗号分隔）")

    a = ap.parse_args(argv)
    if a.cmd == "freshness":
        t = [x.strip() for x in a.tables.split(",")] if a.tables else None
        run_freshness(tables=t, show_all=a.all, stale_days=a.stale)
    elif a.cmd == "peek":
        run_peek(a.table, code=a.code, date=a.date, rows=a.rows, cols=a.cols)
    return 0


if __name__ == "__main__":
    sys.exit(main())
