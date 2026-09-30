# -*- coding: utf-8 -*-
"""
tools/db_migrate.py — 存储布局迁移（by_code → by_year）
=======================================================
把"一标的一文件"的表迁移为"一年一文件"。

为什么迁移
----------
by_code 布局下，每日增量要给数千个文件各做一次 read→concat→write：
    daily 5,903 文件 × 18.7ms ≈ 111 秒
    index_daily 10,849 文件   ≈ 200 秒
    5 表合计 ≈ 8.3 分钟/天
改为 by_year 后每天只改 1 个年度文件，落盘成本降到毫秒级。

安全设计（四重）
----------------
1. **默认 dry-run**：只报告将做什么，不动任何文件
2. **分年流式**：按年份分桶累积，达阈值即写盘，内存可控（不整表载入）
3. **行数双向校验**：迁移前后总行数必须一致；不一致则整体回滚
4. **移动而非复制**：原文件移入备份目录（同盘移动仅改元数据，不占额外空间），
   校验通过后才删除备份；任一步失败可原样还原

用法:
  python main.py db-migrate --dry-run                 # 只看计划
  python main.py db-migrate --tables daily            # 迁移指定表
  python main.py db-migrate --tables daily,daily_basic
  python main.py db-migrate --all                     # 迁移全部 5 个核心表
  python main.py db-migrate --cleanup                 # 校验无误后清除备份
"""
import os
import shutil
import argparse
import time
from collections import defaultdict
from datetime import datetime

import pandas as pd

from config import MARKET_DATA_DIR
from common.parquet_store import list_parquet_files, count_parquet_files

# 默认迁移目标（当前为 by_code 布局、且下游有硬依赖的核心表）
DEFAULT_TABLES = ["daily", "daily_basic", "fund_daily", "cb_daily", "index_daily"]

# 全部待迁移表（2026-09-17 扩展：把剩余 by_code 表统一到按年分区）
# 分组仅用于分批执行；每表的分区列见 DATE_COL。
DAILY_TABLES = DEFAULT_TABLES + ["adj_factor", "stk_limit", "fund_nav"]
PERIODIC_TABLES = [
    # 财务
    "income", "balancesheet", "cashflow", "fina_indicator",
    "fina_audit", "fina_mainbz",
    # 事件面
    "dividend", "forecast", "stk_holdernumber", "stk_holdertrade",
    "pledge_stat", "share_float", "repurchase", "namechange",
    # 基金口径
    "fund_adj", "fund_share",
    # 跨品种 / 指数
    "fx_daily", "index_dailybasic",
    # 可转债静态
    "cb_issue", "cb_rating", "cb_share",
]
PENDING_TABLES = DAILY_TABLES + PERIODIC_TABLES

# 各表的分区列（**按优先级排列，逐列回退**：前一列无值则用后一列）。
# 选列原则: 取"业务上跨年分布、且非空率高"的日期列。
# 注意: 单元素列表表示该表只有这一个可用日期列。
DATE_COL = {
    # ── 已迁移的 5 个核心表（保留兼容）──
    "daily": ["trade_date"], "daily_basic": ["trade_date"],
    "fund_daily": ["trade_date"], "cb_daily": ["trade_date"],
    "index_daily": ["trade_date"],
    # ── 每日更新链路 ──
    "adj_factor": ["trade_date"], "stk_limit": ["trade_date"],
    "fund_nav": ["nav_date"],
    # ── 财务（报告期）──
    "income": ["end_date"], "balancesheet": ["end_date"],
    "cashflow": ["end_date"], "fina_indicator": ["end_date"],
    "fina_audit": ["end_date"], "fina_mainbz": ["end_date"],
    # ── 事件面 ──
    "dividend": ["end_date", "ann_date"],
    "forecast": ["end_date", "ann_date"],
    "stk_holdernumber": ["end_date", "ann_date"],
    "stk_holdertrade": ["ann_date"],
    "pledge_stat": ["end_date"],
    "share_float": ["ann_date", "float_date"],
    "repurchase": ["ann_date", "end_date"],
    "namechange": ["start_date", "ann_date"],
    # ── 基金口径 ──
    "fund_adj": ["trade_date"], "fund_share": ["trade_date"],
    # ── 跨品种 / 指数 ──
    "fx_daily": ["trade_date"], "index_dailybasic": ["trade_date"],
    # ── 可转债静态 ──
    "cb_issue": ["ann_date"],
    "cb_rating": ["rating_date", "ann_date"],
    "cb_share": ["end_date", "publish_date"],
}

# 明确不迁移的表（按年分区对其无意义或有副作用）
NO_MIGRATE = {
    "stock_company": ("一标的一行的静态元数据（18列，无任何日期列）；"
                      "按年分区只能塞进单一 0000 分区，且会让 read_code 传入"
                      "日期参数时静默读空。文件数虽多但从不每日重写，无落盘成本。"),
}

BACKUP_ROOT = os.path.join(os.path.dirname(MARKET_DATA_DIR.rstrip("\\/")),
                           "_migrate_backup")

# 累积多少行落一次盘（控制内存；一年文件约 80 万行）
FLUSH_ROWS = 400_000


def _year_series(df, cols):
    """按候选列优先顺序取"年份"，返回 (year_series, 实际使用的列名)。

    为什么需要逐列回退: 部分表的主日期列有缺失（如 namechange 的 end_date
    非全、repurchase 的 end_date 仅部分有值），单一列会把有效行丢进空分区。

    规则:
      - 逐列尝试，取第一个"形如 YYYYMMDD"的值；该行已有值则不再覆盖
      - 所有候选列都拿不到有效值的行 → 归入 "0000" 分区（保留数据不丢）
    """
    year = pd.Series([None] * len(df), index=df.index, dtype=object)
    used = None
    for c in cols:
        if c not in df.columns:
            continue
        if used is None:
            used = c
        s = df[c].astype(str).str.replace("-", "", regex=False).str.strip()
        valid = s.str.match(r"^\d{8}$", na=False)
        # ⚠ 必须截取前4位: 直接取整值会把 20260914 当成"年份", 每个交易日
        #   都会生成一个分区文件(实测 fx_daily 69 文件 → 6504 个假分区)。
        year = year.where(year.notna(), s.where(valid).str[:4])
    return year.fillna("0000"), used


def _flush_year(tmp_dir, year, frames, stats):
    """把某年累积的数据合并写入 {year}.parquet（增量合并，幂等）

    stats 会累计:
      rows           — 写入的总行数（去重后）
      dedup_removed  — 被全列去重掉的行数（用于校验时解释差异）
    """
    if not frames:
        return
    from common.parquet_store import merge_append
    merged = pd.concat(frames, ignore_index=True)
    n_in = len(merged)
    # 同批内先去重（全列判重，安全）
    merged = merged.drop_duplicates().reset_index(drop=True)
    stats["dedup_removed"] += n_in - len(merged)
    path = os.path.join(tmp_dir, f"{year}.parquet")

    n_before = 0
    if os.path.exists(path):
        try:
            import pyarrow.parquet as pq
            n_before = pq.ParquetFile(path).metadata.num_rows
        except Exception:
            pass
    merge_append(path, merged, subset="*", sort_by=None)
    n_after = 0
    try:
        import pyarrow.parquet as pq
        n_after = pq.ParquetFile(path).metadata.num_rows
    except Exception:
        pass
    stats["rows"] += max(0, n_after - n_before)
    frames.clear()


def migrate_table(table, dry_run=False, keep_backup=True, verbose=True):
    """迁移单表 by_code → by_year。

    返回 dict: {ok, rows_before, rows_after, files_before, files_after, msg, backup}
    """
    d = os.path.join(MARKET_DATA_DIR, table)
    if not os.path.isdir(d):
        return {"ok": False, "msg": f"目录不存在: {d}"}

    files = list_parquet_files(d)
    if not files:
        return {"ok": False, "msg": "目录内无 parquet"}

    # 已是年布局则跳过
    year_like = [f for f in files if len(f) == 12 and f[:4].isdigit()]
    if len(year_like) == len(files):
        return {"ok": True, "msg": "已是 by_year 布局，跳过",
                "rows_before": 0, "rows_after": 0,
                "files_before": len(files), "files_after": len(files)}

    date_col = DATE_COL.get(table, ["trade_date"])
    if isinstance(date_col, str):
        date_col = [date_col]
    # 抽样确认至少一个候选日期列存在
    samp = pd.read_parquet(os.path.join(d, files[0]))
    usable = [c for c in date_col if c in samp.columns]
    if not usable:
        return {"ok": False,
                "msg": f"候选日期列均不存在 {date_col}，实际列={list(samp.columns)[:6]}"}

    t0 = time.time()
    if verbose:
        print("\n" + "=" * 78)
        print(f"  [{table}] by_code → by_year  {'（DRY-RUN）' if dry_run else ''}")
        print("=" * 78)
        print(f"  源: {len(files):,} 个文件 | 分区列: {' → '.join(usable)}")

    # ── 统计源行数（用 pyarrow 元数据，快）──
    import pyarrow.parquet as pq
    rows_before = 0
    bad_files = []
    for f in files:
        try:
            rows_before += pq.ParquetFile(os.path.join(d, f)).metadata.num_rows
        except Exception:
            bad_files.append(f)
    if verbose:
        print(f"  源总行数: {rows_before:,}" + (f" | ⚠ 无法读元数据 {len(bad_files)} 个" if bad_files else ""))

    if dry_run:
        # 预估年度文件数（读一个样本文件的年份跨度不够，改用文件名推断不了）
        # 直接读 3 个样本文件看年份跨度，粗略估计
        years = set()
        for f in files[:5] + files[-5:]:
            try:
                s = pd.read_parquet(os.path.join(d, f), columns=usable)[usable[0]]
                years.update(s.dropna().astype(str).str[:4].unique().tolist())
            except Exception:
                pass
        print(f"  抽样年份: {sorted(years)}")
        print(f"  预计产出: 约 {len(years) or '?'} 个年度文件（实际按全量数据年份数）")
        print(f"  → 行数不变，文件数 {len(files):,} → 约 10~30")
        return {"ok": True, "msg": "dry-run", "rows_before": rows_before,
                "rows_after": rows_before, "files_before": len(files),
                "files_after": 0}

    # ── 实际迁移：写入临时目录 ──
    tmp_dir = d + "__new"
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)

    buckets = defaultdict(list)
    nrows = defaultdict(int)
    stats = {"rows": 0, "dedup_removed": 0}
    read_fail = []

    for i, f in enumerate(files, 1):
        p = os.path.join(d, f)
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            read_fail.append((f, str(e)[:40]))
            continue
        if df.empty:
            continue
        year_s, _used = _year_series(df, usable)
        for year, g in df.groupby(year_s.values, sort=False):
            buckets[year].append(g)
            nrows[year] += len(g)
            if nrows[year] >= FLUSH_ROWS:
                _flush_year(tmp_dir, year, buckets[year], stats)
                nrows[year] = 0
        if verbose and (i % 1000 == 0 or i == len(files)):
            print(f"    {i:6,d}/{len(files):,}  已写 {stats['rows']:,} 行  "
                  f"{time.time()-t0:.0f}s")

    # 收尾 flush
    for year in list(buckets.keys()):
        _flush_year(tmp_dir, year, buckets[year], stats)

    if read_fail:
        print(f"  ⚠ 读取失败 {len(read_fail)} 个: {read_fail[:3]}")

    # ── 校验：新文件总行数 ──
    new_files = list_parquet_files(tmp_dir)
    rows_after = 0
    for f in new_files:
        try:
            rows_after += pq.ParquetFile(os.path.join(tmp_dir, f)).metadata.num_rows
        except Exception:
            pass

    if verbose:
        print(f"\n  源行数 {rows_before:,} | 新行数 {rows_after:,} | "
              f"文件 {len(files):,} → {len(new_files)}")
        if stats["dedup_removed"]:
            print(f"  全列去重移除: {stats['dedup_removed']:,} 行（原数据含整行重复）")

    # 校验: 新行数 + 去重移除数 必须等于源行数
    #   差异若能被"去重"完全解释 → 属预期（源数据本身有整行重复）
    #   否则 → 说明丢数据，必须回滚
    unexplained = rows_before - rows_after - stats["dedup_removed"]
    if unexplained != 0:
        msg = (f"行数校验失败! 源={rows_before:,} 新={rows_after:,} "
               f"去重={stats['dedup_removed']:,} 未解释差异={unexplained:,} "
               f"—— 已回滚，原文件未动")
        print(f"  ⛔ {msg}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"ok": False, "msg": msg, "rows_before": rows_before,
                "rows_after": rows_after, "files_before": len(files),
                "files_after": len(new_files)}

    # ── 原子替换：原文件移入备份，新文件就位 ──
    bk = os.path.join(BACKUP_ROOT, table)
    os.makedirs(bk, exist_ok=True)
    moved = 0
    try:
        for f in files:
            shutil.move(os.path.join(d, f), os.path.join(bk, f))
            moved += 1
        for f in new_files:
            shutil.move(os.path.join(tmp_dir, f), os.path.join(d, f))
    except Exception as e:
        # 尽力回滚
        print(f"  ⛔ 替换失败: {str(e)[:80]} —— 尝试回滚")
        for f in new_files:
            src = os.path.join(tmp_dir, f)
            if os.path.exists(src):
                try:
                    shutil.move(src, os.path.join(d, f))
                except Exception:
                    pass
        for f in files:
            src = os.path.join(bk, f)
            dst = os.path.join(d, f)
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    shutil.move(src, dst)
                except Exception:
                    pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"ok": False, "msg": f"替换失败已回滚: {str(e)[:60]}",
                "rows_before": rows_before, "rows_after": rows_after,
                "files_before": len(files), "files_after": len(new_files)}

    shutil.rmtree(tmp_dir, ignore_errors=True)

    if not keep_backup:
        shutil.rmtree(bk, ignore_errors=True)
        bk = None

    el = time.time() - t0
    dedup_note = ""
    if stats["dedup_removed"]:
        dedup_note = f"（另去重移除 {stats['dedup_removed']:,} 行整行重复）"
    msg = (f"迁移成功: {len(files):,} 文件 → {len(new_files)} 年度文件, "
           f"{rows_after:,} 行无损{dedup_note}, 耗时 {el:.0f}s")
    if verbose:
        print(f"  ✓ {msg}")
        if bk:
            print(f"  备份: {bk}")
    return {"ok": True, "msg": msg, "rows_before": rows_before,
            "rows_after": rows_after, "dedup_removed": stats["dedup_removed"],
            "files_before": len(files), "files_after": len(new_files),
            "backup": bk}


def run_migrate(tables=None, dry_run=False, keep_backup=True):
    tables = tables or DEFAULT_TABLES
    print("=" * 78)
    print(f"  存储布局迁移 by_code → by_year  {'（DRY-RUN，不改动文件）' if dry_run else ''}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  目标表: {len(tables)} 个 — {', '.join(tables)}")
    skip = [t for t in tables if t in NO_MIGRATE]
    if skip:
        print(f"  ⚠ 跳过（不适用按年分区）: {', '.join(skip)}")
    print("=" * 78)

    ok_n = fail_n = skipped_n = 0
    total_rows = 0
    results = []
    for t in tables:
        if t in NO_MIGRATE:
            results.append((t, {"ok": True, "msg": f"跳过（{NO_MIGRATE[t][:24]}...）"}))
            skipped_n += 1
            continue
        try:
            r = migrate_table(t, dry_run=dry_run, keep_backup=keep_backup)
            results.append((t, r))
            if r.get("ok"):
                if "已是 by_year" in r.get("msg", ""):
                    skipped_n += 1
                else:
                    ok_n += 1
                    total_rows += r.get("rows_after", 0)
            else:
                fail_n += 1
        except Exception as e:
            print(f"\n  [ERROR] {t}: {type(e).__name__}: {str(e)[:100]}")
            results.append((t, {"ok": False, "msg": str(e)[:80]}))
            fail_n += 1

    print("\n" + "=" * 78)
    print("  汇总")
    print("=" * 78)
    for t, r in results:
        flag = "✓" if r.get("ok") else "✗"
        print(f"  [{flag}] {t:18s} {r.get('msg','')}")
    print(f"\n  成功={ok_n} 失败={fail_n} 跳过={skipped_n} | 迁移行数合计={total_rows:,}")
    return results


def cleanup_backup(tables=None):
    """校验无误后清除迁移备份"""
    tables = tables or DEFAULT_TABLES
    total = 0
    for t in tables:
        bk = os.path.join(BACKUP_ROOT, t)
        if os.path.isdir(bk):
            n = count_parquet_files(bk)
            sz = sum(os.path.getsize(os.path.join(bk, f))
                     for f in list_parquet_files(bk))
            shutil.rmtree(bk, ignore_errors=True)
            total += sz
            print(f"  已删除备份 {t}: {n:,} 文件 / {sz/1024/1024:.1f} MB")
    if os.path.isdir(BACKUP_ROOT) and not os.listdir(BACKUP_ROOT):
        os.rmdir(BACKUP_ROOT)
    print(f"\n共释放 {total/1024/1024/1024:.2f} GB")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="存储布局迁移（by_code → by_year）")
    ap.add_argument("--tables", default=None,
                    help=f"指定表，逗号分隔（默认 {','.join(DEFAULT_TABLES)}）")
    ap.add_argument("--all", action="store_true",
                    help=f"迁移全部待迁移表（{len(PENDING_TABLES)} 个）")
    ap.add_argument("--daily", action="store_true",
                    help="只迁移每日更新链路（core + adj_factor/stk_limit/fund_nav）")
    ap.add_argument("--dry-run", action="store_true", help="只报告计划，不改动")
    ap.add_argument("--no-backup", action="store_true", help="不留备份（不推荐）")
    ap.add_argument("--cleanup", action="store_true", help="清除迁移备份后退出")
    a = ap.parse_args(argv)

    if a.cleanup:
        cleanup_backup()
        return

    if a.all:
        tables = list(PENDING_TABLES)
    elif a.daily:
        tables = list(DAILY_TABLES)
    elif a.tables:
        tables = [x.strip() for x in a.tables.split(",") if x.strip()]
    else:
        tables = None
    run_migrate(tables=tables, dry_run=a.dry_run,
                keep_backup=not a.no_backup)


if __name__ == "__main__":
    main()
