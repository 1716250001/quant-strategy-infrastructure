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

from common import reader
from config import MARKET_DATA_DIR

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
        import pandas as pd
        import pyarrow.parquet as pq
        names = list(pq.ParquetFile(os.path.join(dd, files[-1])).schema_arrow.names)
        col = next((c for c in ([dc] if dc else []) + list(_DATE_FALLBACK)
                    if c in names), None)
        if not col:
            return None
        s = pd.read_parquet(os.path.join(dd, files[-1]), columns=[col])[col]
        return str(s.max()) if len(s) else None
    except Exception as exc:
        print(f"[quicklook] 读最新日期失败 {dd}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


# ============================================================
# freshness
# ============================================================
def run_freshness(tables=None, show_all=False, stale_days=None, verbose=True,
                  as_json=False):
    """[DEPRECATED v4.10] 旧版单条件实现，已降级为 tools.db.freshness 的透传代理。

    ⚠ **同名不同义（必须读）**：
      - 旧版：默认 18 张核心表；`stale_days` = 落后超过 N 个**交易日**。
      - 新版：默认全部 208 张表；`stale_days` = ds（内容落后**自然日**）≥ N。
    口径已切到新版自然日双条件；新增调用一律走 `tools.db.freshness.run()`。
    保留签名是为了 `main()`（本文件）与历史调用点不炸。
    """
    from tools.db import freshness as _f

    _f._UNREADABLE.clear()              # K′：按轮隔离，防跨调用累积
    only = set(tables) if tables else None
    rows = _f.build_rows(only)
    if stale_days is not None:
        # v4.10：ds（自然日）≥ N；ds 为 None（无日期）不满足「落后 N 天」⇒ 不显示
        rows = [r for r in rows
                if r["ds"] is not None and r["ds"] >= stale_days]
    n_unread = len(_f._UNREADABLE)
    if as_json:
        from common.jsonio import print_json
        n_alert = sum(1 for r in rows if r["verdict"] in ("红", "黄"))
        print_json({"ok": True, "rows": rows, "n_alert": n_alert,
                    "n_unreadable": n_unread}, indent=1)
    else:
        print(_f.render(rows))
    _f._report_unreadable()
    return rows


# ============================================================
# peek
# ============================================================
def run_peek(table, code=None, date=None, rows=10, cols=None, verbose=True,
             as_json=False):
    """查看某张表的实际数据（默认尾部 N 行）。

    as_json: stdout 输出机器可读 JSON（CLI --json；2026-09-30 P3 统一）
    """
    from common.jsonio import print_json

    def _empty(error=None):
        out = {"table": table, "code": code, "date": date,
               "rows": 0, "columns": [], "data": []}
        if error:
            out["error"] = error
        print_json(out, indent=1)

    d = os.path.join(MARKET_DATA_DIR, table)
    if not os.path.isdir(d):
        if as_json:
            _empty(f"表不存在: {table}")
        else:
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
        if as_json:
            _empty("无匹配数据")
        else:
            print(f"  （无匹配数据）table={table} code={code} date={date}")
        return df

    if cols:
        keep = [c for c in cols.split(",") if c.strip() in df.columns]
        if keep:
            df = df[keep]

    if as_json:
        n = min(int(rows or 10), len(df))
        print_json({
            "table": table, "code": code, "date": date,
            "rows": len(df),
            "columns": [str(c) for c in df.columns],
            "data": df.tail(n).to_dict(orient="records"),
            "date_col": dc,
            "date_range": ([str(df[dc].min()), str(df[dc].max())]
                           if dc in df.columns else None),
        }, indent=1)
        return df

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
    p.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    p = sub.add_parser("peek", help="查看表数据")
    p.add_argument("table", help="表名")
    p.add_argument("--code", default=None, help="标的代码")
    p.add_argument("--date", default=None, help="日期 YYYYMMDD")
    p.add_argument("--rows", type=int, default=10, help="显示行数（默认10）")
    p.add_argument("--cols", default=None, help="只显示指定列（逗号分隔）")
    p.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    a = ap.parse_args(argv)
    if a.cmd == "freshness":
        t = [x.strip() for x in a.tables.split(",")] if a.tables else None
        run_freshness(tables=t, show_all=a.all, stale_days=a.stale,
                      as_json=a.json)
    elif a.cmd == "peek":
        run_peek(a.table, code=a.code, date=a.date, rows=a.rows, cols=a.cols,
                 as_json=a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
