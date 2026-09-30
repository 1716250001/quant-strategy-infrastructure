# -*- coding: utf-8 -*-
"""
common/parquet_store.py — 按代码分文件的 Parquet 增量存储
==========================================================
统一此前散落在 intraday_update / redtide_supply / fund_nav_update 三处的
"读 existing → concat → drop_duplicates → 排序 → 写回" 重复实现（12 处）。

核心语义：
  - 每个 ts_code 一个 parquet 文件，行主键为 subset（如 trade_date / nav_date）
  - 合并时按 subset 去重，keep="last" 保证新数据覆盖旧数据
  - 列顺序与既有文件对齐，避免 Parquet schema 漂移
"""
import os

import pandas as pd

from common.paths import ensure_dir


def _invalidate_reader_cache(path_or_dir, table=None):
    """写入后失效 reader 的布局/代码清单缓存。

    为什么必须做（2026-09-19）:
      reader 的 list_codes / codes_from_store / detect_layout 都带了缓存，
      但**写入数据会改变结果**（新代码入库、空目录首次出现年度文件会改变布局判定）。
      若不清缓存，同进程内后续读取会拿到陈旧值 —— 这类"写后读旧"不会报错。

    ⚠ 延迟导入 reader：reader 依赖本模块的 list_parquet_files，
      顶层互相 import 会循环导入。

    参数:
        path_or_dir: 写入目标（文件或目录）
        table:       显式指定表名（优先）；None 则从路径推断
    """
    try:
        from common import reader
    except Exception:
        return
    try:
        if not table:
            p = str(path_or_dir).rstrip("\\/")
            if p.endswith(".parquet"):
                table = os.path.basename(os.path.dirname(p))   # 文件 → 父目录名
            else:
                table = os.path.basename(p)                    # 目录 → 目录名
        if table:
            reader.clear_cache(table)
    except Exception:
        pass


# ============================================================
# 目录扫描工具
# ============================================================
def list_parquet_files(dir_path, sort=False, with_dir=False):
    """列出目录下的 parquet 文件（仅文件名）。

    此前该逻辑在 14 个模块里各写一遍(listdir + endswith 过滤),
    统一到此以便统一行为(目录不存在时返回空列表而非抛异常)。

    参数:
        dir_path: 目录路径（不存在则返回 []）
        sort:     True 则按文件名排序（年份分区场景需要）
        with_dir: True 则返回完整路径
    返回:
        文件名列表（或完整路径列表）
    """
    if not dir_path or not os.path.isdir(dir_path):
        return []
    files = [f for f in os.listdir(dir_path) if f.endswith(".parquet")]
    if sort:
        files.sort()
    if with_dir:
        return [os.path.join(dir_path, f) for f in files]
    return files


def count_parquet_files(dir_path):
    """统计目录下 parquet 文件数（目录不存在返回 0）"""
    return len(list_parquet_files(dir_path))


def dir_size_bytes(dir_path, pattern=".parquet"):
    """统计目录（含子目录）内匹配文件的总字节数。"""
    total = 0
    if not os.path.isdir(dir_path):
        return 0
    for dp, _dn, fn in os.walk(dir_path):
        for f in fn:
            if f.endswith(pattern):
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    return total


def merge_append(path, new_df, subset, sort_by=None, keep="last"):
    """把 new_df 合并进 path，按 subset 去重后写回。

    参数:
        path: parquet 文件路径（不存在则新建）
        new_df: 待追加的 DataFrame
        subset: 去重主键。
                - 字符串/列表: 按指定列判重
                - None 或 "*": **全列判重**(安全兜底, 适用于单日多行
                  且主键不易穷举的接口, 如 top_inst / index_weight)
                注意: 若单日有多行而 subset 只给了日期列, 同一天的多行
                会被判为重复而丢失 —— 这类接口必须用复合主键或 "*"。
        sort_by: 排序列（None 则保持插入顺序）
        keep: 去重保留策略，默认 last（新数据优先）
    返回:
        (changed: bool, added_rows: int)
    """
    if new_df is None or len(new_df) == 0:
        return False, 0

    if subset in (None, "*"):
        keys = None                      # pandas: subset=None → 全列判重
    else:
        keys = [subset] if isinstance(subset, str) else list(subset)

    if os.path.exists(path):
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=keys, keep=keep)
        if sort_by and sort_by in combined.columns:
            combined = combined.sort_values(sort_by)
        combined = combined.reset_index(drop=True)

        added = len(combined) - len(existing)
        if added <= 0:
            return False, 0
        # 列顺序与既有文件对齐，保持文件结构稳定
        if set(combined.columns) == set(existing.columns):
            combined = combined[list(existing.columns)]
        combined.to_parquet(path, index=False)
        _invalidate_reader_cache(path)          # 写后失效读取缓存
        return True, added

    # 新文件
    df = new_df
    if sort_by and sort_by in df.columns:
        df = df.sort_values(sort_by)
    df = df.reset_index(drop=True)
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    df.to_parquet(path, index=False)
    _invalidate_reader_cache(path)
    return True, len(df)


class LayoutGuardError(RuntimeError):
    """试图用 by_code 写入器写按年分区的表 —— 会静默破坏布局。"""


def _assert_by_code_target(dir_path):
    """运行时守卫: 目标目录若已是按年分区, 拒绝按标的写入。

    为什么必须硬拦而不是只在文档里写警告:
      upsert_grouped 写 {code}.parquet；若目录已是 by_year，会产生
      「年度文件 + 代码文件」混合布局 → reader.detect_layout 落到 by_code
      → read_code 找不到文件（恒空）、list_codes 返回 ["2026", ...] 这类垃圾。
      全程不报错。实证: backfill 的 by_code 写路径 + 17 个“mode=by_code
      但磁盘已迁 by_year”的表，一旦有新标的入库即触发。
    仅文档警告拦不住这类问题——必须让它在写入前就报错。
    """
    if not os.path.isdir(dir_path):
        return
    try:
        names = os.listdir(dir_path)
    except OSError:
        return
    for f in names:
        if f.endswith(".parquet") and len(f) == 12 and f[:4].isdigit():
            raise LayoutGuardError(
                f"拒绝按标的写入: {dir_path} 已是按年分区（发现 {f}）。\n"
                f"    按年分区的表请改用 upsert_by_year / YearBatchWriter。\n"
                f"    仅 stock_company（静态元数据、无日期列）适用 upsert_grouped。\n"
                f"    确需混写请显式传 allow_mixed=True（后果自负）。"
            )


def upsert_grouped(df, dir_path, key_col="ts_code", subset="trade_date",
                   sort_by="trade_date", on_error=None, allow_mixed=False):
    """按 key_col 分组，逐代码合并写入 dir_path/{code}.parquet。

    ⚠ 使用前先确认目标表的布局（2026-09-17 全库已统一为按年分区）：
      本函数是 **by_code 布局** 的写入器，对已迁移为 by_year 的表使用它会
      在年度目录里凭空生成数千个小文件，并把 reader 的布局探测打回 by_code
      （表现为下游读数全面降级，且不报错）。
      已迁移的表请改用 upsert_by_year / YearBatchWriter。
      全库唯一仍适用本函数的表：stock_company（一标一行的静态元数据）。

    参数:
        df: 全市场当日/区间数据（含 key_col 列）
        dir_path: 目标目录
        key_col: 分组列（文件名来源）
        subset: 行去重主键
        sort_by: 排序列
        on_error: 可选回调 on_error(code, exception)
        allow_mixed: 默认 False —— 目标目录已是按年分区时**报错拒绝**。
                 这个默认值是刻意的: 混写不会立即报错, 但会让 reader
                 布局探测降级、下游读数静默变空。
    返回:
        {"processed": n, "updated": n, "new": n, "unchanged": n,
         "fail": n, "added_rows": n, "codes": n}
    """
    if df is None or len(df) == 0:
        return {"processed": 0, "updated": 0, "new": 0, "unchanged": 0,
                "fail": 0, "added_rows": 0, "codes": 0}

    if not allow_mixed:
        _assert_by_code_target(dir_path)

    ensure_dir(dir_path)
    updated = new_files = fail = added_rows = 0

    for code, group in df.groupby(key_col):
        fpath = os.path.join(dir_path, f"{code}.parquet")
        is_new = not os.path.exists(fpath)
        try:
            changed, added = merge_append(fpath, group, subset=subset, sort_by=sort_by)
            if is_new:
                new_files += 1
            elif changed:
                updated += 1
            added_rows += added
        except Exception as e:
            fail += 1
            if on_error is not None:
                on_error(code, e)
            else:
                print(f"  [ERROR] {code}: {e}")

    processed = updated + new_files + fail
    # unchanged = 处理成功但无新增行的既有文件
    total = int(df[key_col].nunique())
    unchanged = total - processed
    return {
        "processed": processed, "updated": updated, "new": new_files,
        "unchanged": max(unchanged, 0), "fail": fail,
        "added_rows": added_rows, "codes": total,
    }


def read_column(path, column):
    """只读单列（用于快速取最新日期等）"""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_parquet(path, columns=[column])
        return df[column]
    except Exception:
        return None


# ============================================================
# 按年分区存储 (2026-09-16 新增)
# ============================================================
def upsert_by_year(df, dir_path, date_col="trade_date", subset=None,
                   sort_by=None, on_error=None):
    """按【年份】分区写入 dir_path/{YYYY}.parquet。

    为什么需要它:
      "按日拉全市场"类接口(moneyflow / margin_detail / fut_daily ...)每天
      有数千只标的。若按 ts_code 拆成数千个小文件, 每天都要对数千个文件
      做"读→合并→写回", I/O 被放大数千倍。实测 moneyflow 只有 1 天/分钟,
      单接口需 44 小时, 完全跑不完。
      改为按年分区后, 每天只需对 1 个文件(当年)做一次读改写。

    参数:
        df:       当日/当期全市场数据
        dir_path: 目标目录
        date_col: 分区依据列(取其前4位作年份)。**若该列不存在则回退为单文件**。
        subset:   行去重主键(None=沿用默认; "*"=全列判重)
        sort_by:  排序列
        on_error: 可选回调 on_error(year, exception)
    返回:
        {"partitions": n, "rows": n, "added": n, "fail": n}
    """
    if df is None or len(df) == 0:
        return {"partitions": 0, "rows": 0, "added": 0, "fail": 0}

    ensure_dir(dir_path)

    # 无日期列 → 回退单文件(如 new_share 只有 ipo_date)
    if date_col not in df.columns:
        raise KeyError(f"按年分区需要列 '{date_col}', 实际列: {list(df.columns)[:8]}")

    work = df.copy()
    # 年份: 兼容 YYYYMMDD 与 YYYY-MM-DD
    years = work[date_col].astype(str).str.replace("-", "", regex=False).str[:4]
    work = work.assign(_year=years)

    n_part = added = fail = 0
    for year, group in work.groupby("_year"):
        group = group.drop(columns=["_year"])
        path = os.path.join(dir_path, f"{year}.parquet")
        try:
            if subset == "*":
                sub = list(group.columns)
            else:
                sub = subset or date_col
            srt = sort_by or (sub[0] if isinstance(sub, list) else sub)
            if srt not in group.columns:
                srt = date_col
            _, add = merge_append(path, group, subset=sub, sort_by=srt)
            n_part += 1
            added += add
        except Exception as e:
            fail += 1
            if on_error is not None:
                on_error(year, e)
            else:
                print(f"  [ERROR] 分区 {year}: {str(e)[:80]}")

    return {"partitions": n_part, "rows": len(df), "added": added, "fail": fail}


def migrate_to_year_partitions(dir_path, date_col="trade_date", subset=None,
                               key_col="ts_code", dry_run=False, backup=False):
    """把已按 key_col 拆分的小文件合并为按年分区大表。

    安全策略(默认无损):
      1. 先读全部小文件并合并
      2. **校验合并前后总行数一致**
      3. 写入 {YYYY}.parquet
      4. 再次校验所有年份文件总行数 == 原总行数
      5. 只有第4步通过才删除原小文件
      任一步失败 → 保留原文件, 返回失败原因

    混合布局支持(2026-09-18 修复):
      目录里若**同时存在**年份文件与待合并小文件(如 margin 曾有
      margin.parquet + 2026.parquet 并存), 年份文件也必须一并读入合并。
      旧实现只读 small_files 却会覆盖 year_files → 静默丢数据
      (实证: 会丢掉 2026.parquet 里的 0916/0917 共 6 行)。
      现合并全部文件, 行数校验对全部文件生效。
      注: 若各文件间存在重复行, 去重后行数会少于原总行数, 校验失败并
      回滚(保守设计, 宁可不动也不误删)。
    """
    if not os.path.isdir(dir_path):
        return {"ok": False, "rows_before": 0, "rows_after": 0, "parts": [],
                "files_removed": 0, "msg": "目录不存在"}

    all_files = [f for f in os.listdir(dir_path) if f.endswith(".parquet")]
    # 分离: 已是年份分区文件 vs 待合并的小文件
    year_files = [f for f in all_files if len(f) == 12 and f[:4].isdigit()]
    small_files = [f for f in all_files if f not in year_files]
    if not small_files:
        return {"ok": True, "rows_before": 0, "rows_after": 0, "parts": [f[:4] for f in year_files],
                "files_removed": 0, "msg": "无待合并文件(已是年份分区)"}

    frames = []
    rows_before = 0
    bad = []
    # ⚠ 关键: 若目录里同时存在年份文件, 必须一并读入合并。
    #   否则替换阶段会用一个"只含小文件数据"的新年份文件覆盖现有年份文件,
    #   造成静默丢数据。
    to_read = small_files + year_files
    for f in to_read:
        try:
            d = pd.read_parquet(os.path.join(dir_path, f))
            frames.append(d)
            rows_before += len(d)
        except Exception as e:
            bad.append(f"{f}: {str(e)[:40]}")
    if bad:
        return {"ok": False, "rows_before": rows_before, "rows_after": 0, "parts": [],
                "files_removed": 0, "msg": f"读取失败 {len(bad)} 个: {bad[:3]}"}
    if not frames:
        return {"ok": False, "rows_before": 0, "rows_after": 0, "parts": [],
                "files_removed": 0, "msg": "无可读数据"}

    merged = pd.concat(frames, ignore_index=True)
    if date_col not in merged.columns:
        return {"ok": False, "rows_before": rows_before, "rows_after": 0, "parts": [],
                "files_removed": 0,
                "msg": f"缺少分区列 {date_col}, 实际列={list(merged.columns)[:8]}"}

    if dry_run:
        y = merged[date_col].astype(str).str[:4]
        return {"ok": True, "rows_before": rows_before, "rows_after": rows_before,
                "parts": sorted(y.unique().tolist()), "files_removed": 0,
                "msg": f"[DRY-RUN] 将合并 {len(to_read)} 文件"
                       f"(小文件{len(small_files)}+年份文件{len(year_files)}) "
                       f"→ {y.nunique()} 个年份分区"}

    # 写入年份分区
    tmp_dir = dir_path + "__yearnew"
    ensure_dir(tmp_dir)
    work = merged.assign(_year=merged[date_col].astype(str).str.replace("-", "", regex=False).str[:4])
    parts = []
    for year, g in work.groupby("_year"):
        g = g.drop(columns=["_year"])
        path = os.path.join(tmp_dir, f"{year}.parquet")
        sub = list(g.columns) if subset == "*" else (subset or date_col)
        srt = sub[0] if isinstance(sub, list) else sub
        if srt not in g.columns:
            srt = date_col
        merge_append(path, g, subset=sub, sort_by=srt)
        parts.append(year)

    # 校验: 新文件总行数必须 == 原总行数
    rows_after = 0
    for f in os.listdir(tmp_dir):
        if f.endswith(".parquet"):
            rows_after += len(pd.read_parquet(os.path.join(tmp_dir, f)))

    if rows_after != rows_before:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"ok": False, "rows_before": rows_before, "rows_after": rows_after,
                "parts": parts, "files_removed": 0,
                "msg": f"行数校验失败! 原={rows_before:,} 新={rows_after:,} — 已回滚, 原文件保留"}

    # 校验通过 → 移动新文件到目标目录, 删除旧小文件
    import shutil
    for f in os.listdir(tmp_dir):
        shutil.move(os.path.join(tmp_dir, f), os.path.join(dir_path, f))
    shutil.rmtree(tmp_dir, ignore_errors=True)

    removed = 0
    for f in small_files:
        try:
            os.remove(os.path.join(dir_path, f))
            removed += 1
        except Exception:
            pass

    # 布局已改变（小文件→年度文件），必须失效 reader 缓存
    _invalidate_reader_cache(dir_path)

    return {"ok": True, "rows_before": rows_before, "rows_after": rows_after,
            "parts": sorted(parts), "files_removed": removed,
            "msg": f"迁移成功: {len(to_read)}文件"
                   f"(小文件{len(small_files)}+年份文件{len(year_files)}) "
                   f"→ {len(parts)}年份分区, {rows_before:,} 行无损"}


# ============================================================
# 按年批量写入器 (2026-09-17 新增)
# ============================================================
class YearBatchWriter:
    """按年分桶累积后批量落盘，减少 parquet 读改写次数。

    为什么需要它 —— 实测数据:
      按年分区下，追加"一天"的数据要对整个年份文件做
      read → concat → dedup → sort → write。该成本随文件线性增长:
          moneyflow 文件规模     单次落盘耗时
          空                     0.03s
          5 万行                 0.10s
          32 万行                0.49s
          82 万行(年末)           1.49s
      而单次网络请求仅约 0.4s —— 落盘反而成了瓶颈(3.7 倍)。
      逐天追加 N 天 = N 次 O(M) 代价；批量后只需 1 次 O(M+N)。

    用法:
        w = YearBatchWriter(dir_path, date_col="trade_date", subset="*",
                            on_flushed=lambda keys: ...)
        w.add(df, key)      # 累积；达到阈值自动 flush
        w.flush()           # 收尾

    设计要点:
      - on_flushed 回调在**每次 flush 成功后**触发, 传入本次已落盘的 key 列表。
        调用方应在此回调里标记断点 —— 保证"先落盘、后记断点", 不会因
        崩溃导致"断点说完成但数据没写"。
      - 累积在内存中, 阈值按"天数"与"行数"双重控制, 避免单年缓冲过大。
    """

    def __init__(self, dir_path, date_col="trade_date", subset=None, sort_by=None,
                 flush_days=20, flush_rows=500_000,
                 on_flushed=None, on_error=None):
        self.dir_path = dir_path
        self.date_col = date_col
        self.subset = subset
        self.sort_by = sort_by
        self.flush_days = max(int(flush_days), 1)
        self.flush_rows = max(int(flush_rows), 1)
        self.on_flushed = on_flushed
        self.on_error = on_error
        self._buf = {}          # {年份: [df, ...]}
        self._keys = {}         # {年份: [key, ...]}
        self._nrows = {}        # {年份: 累积行数}
        self.flushed_frames = 0
        self.flushed_rows = 0

    # ---------- 内部 ----------
    def _year_of(self, df):
        y = df[self.date_col].astype(str).str.replace("-", "", regex=False).str[:4]
        u = y.unique()
        return u[0] if len(u) == 1 else None

    def _do_flush(self, year):
        frames = self._buf.pop(year, [])
        keys = self._keys.pop(year, [])
        self._nrows.pop(year, None)
        if not frames:
            return
        merged = pd.concat(frames, ignore_index=True)
        if len(frames) > 1:
            merged = merged.drop_duplicates().reset_index(drop=True)
        try:
            r = upsert_by_year(merged, self.dir_path, date_col=self.date_col,
                               subset=self.subset, sort_by=self.sort_by)
        except Exception as e:
            # 落盘失败 → 不触发 on_flushed, 断点也不记, 下次续跑重拉
            if self.on_error is not None:
                self.on_error(year, e)
            else:
                print(f"  [ERROR] 批量落盘失败 {year}: {str(e)[:80]}")
            return
        # ⚠ 2026-09-22 修复（P0）: upsert_by_year 对"某个年份分区写失败"是
        #   **不抛异常、只把 fail 计数返回**（见 parquet_store.upsert_by_year 的
        #   except 分支）。原实现忽略该返回值 → 部分年份没写进去却照样触发
        #   on_flushed → 断点被标记为完成，该批数据永不重拉。
        #   此处必须显式拦截: fail>0 一律视为整体失败。
        if r.get("fail"):
            msg = f"{r['fail']} 个年份分区写入失败（partitions={r.get('partitions')}）"
            if self.on_error is not None:
                self.on_error(year, RuntimeError(msg))
            else:
                print(f"  [ERROR] 批量落盘部分失败 {year}: {msg}")
            return
        self.flushed_frames += 1
        self.flushed_rows += len(merged)
        if self.on_flushed is not None and keys:
            self.on_flushed(keys)

    # ---------- 对外 ----------
    def add(self, df, key):
        """累积一帧数据；达到阈值则自动 flush 对应年份。"""
        if df is None or len(df) == 0:
            return
        if self.date_col not in df.columns:
            # 无分区列 → 直接走单次写入, 不参与批量
            try:
                r = upsert_by_year(df, self.dir_path, date_col=self.date_col,
                                   subset=self.subset, sort_by=self.sort_by)
            except Exception as e:
                if self.on_error is not None:
                    self.on_error(None, e)
                return
            # fail>0 = 有分区没写进去 → 不得记断点（同 _do_flush 说明）
            if r.get("fail"):
                if self.on_error is not None:
                    self.on_error(None, RuntimeError(f"{r['fail']} 个年份分区写入失败"))
                return
            if self.on_flushed is not None:
                self.on_flushed([key])
            return

        year = self._year_of(df)
        if year is None:
            # 跨年数据(如区间拉取) → 不批, 直接写
            try:
                r = upsert_by_year(df, self.dir_path, date_col=self.date_col,
                                   subset=self.subset, sort_by=self.sort_by)
            except Exception as e:
                if self.on_error is not None:
                    self.on_error(None, e)
                return
            # fail>0 = 有分区没写进去 → 不得记断点（同 _do_flush 说明）
            if r.get("fail"):
                if self.on_error is not None:
                    self.on_error(None, RuntimeError(f"{r['fail']} 个年份分区写入失败"))
                return
            if self.on_flushed is not None:
                self.on_flushed([key])
            return

        self._buf.setdefault(year, []).append(df)
        self._keys.setdefault(year, []).append(key)
        self._nrows[year] = self._nrows.get(year, 0) + len(df)

        if (len(self._keys[year]) >= self.flush_days
                or self._nrows[year] >= self.flush_rows):
            self._do_flush(year)

    def flush(self):
        """把所有未落盘的分桶写入磁盘。"""
        for year in list(self._buf.keys()):
            self._do_flush(year)

    def stat(self):
        return {"frames": self.flushed_frames, "rows": self.flushed_rows}
