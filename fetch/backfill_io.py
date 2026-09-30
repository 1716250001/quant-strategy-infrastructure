# -*- coding: utf-8 -*-
"""
fetch/backfill_io.py — 补数 IO 层
==================================
从 fetch/backfill.py 拆出（原文件 762 行、5 个职责混杂）。本层只负责
"数据怎么取、怎么存"，**不含任何任务规划逻辑**(见 backfill_spec.py)。

包含:
  1. fetch_paged_by_date()  按日军分页拉取（防静默截断）
  2. save_df()              按布局落盘（按年分区 / 按标的 / 单文件）
  3. ensure_meta()          前置基础表(fx_obasic/hk_basic)按需拉取

⚠ 两条关键纪律（均为实测踩坑，勿改）:

  ① 分页防静默截断
     部分接口单日返回量超服务端单次上限，不传 limit/offset 会**静默截断**:
       fut_holding 默认返 4,000, 真实 10,650（丢 62%）
       opt_daily   默认返 15,000, 真实 25,801（丢 42%）

  ② `None` 与「空 DataFrame」必须区分
     这是最隐蔽的坑。实测 hk_hold 有 56 个港股通休市日(圣诞节/重阳节等，
     A股开市但港股通关闭)，现拉返回的是**空 DataFrame**(调用成功)，而非失败。
     若把两者都归一成 None，这些日期将永远不记断点、每次续跑都重拉:
       None          = 调用失败（限频/网络/无权限）→ 调用方不应记断点
       空 DataFrame  = 调用成功但该日无数据         → 调用方应记断点
"""
import os
from datetime import datetime

import pandas as pd

from config import (META_DIR, BACKFILL_PAGE_CONF, TABLE_DATE_COL,
                    BACKFILL_TARGETS, DAILY_UPDATE_EXTRA)
from common.paths import ensure_dir
from common.parquet_store import upsert_grouped, merge_append, upsert_by_year
from fetch.base import ts_call_with_retry


# ============================================================
# 0. 存储布局判定（取数方式 ≠ 存储布局）
# ============================================================
# ⚠ 这两件事必须分开（2026-09-18 修正）:
#   mode     = 怎么取数（by_code 按标的逐个调 / by_date 按日拉全市场）
#   layout   = 怎么存盘（by_year 按年分区 / by_code 按标的单文件）
#   历史上二者被混为一谈：mode="by_code" 既决定了逐个标的调接口，
#   也直接决定了写 {code}.parquet。按年分区迁移只改了磁盘数据、
#   没同步这个隐含假定，于是 17 个表出现「配置说 by_code、磁盘是 by_year」。
#   后果：一旦有未完成标的（如新上市股进 stock_all）跑 backfill，
#   就会往年度目录里写 {code}.parquet → 布局探测翻转成 by_code →
#   read_code 找不到文件、list_codes 返回 ["2026", ...] 这类垃圾。
#
# 权威依据 = config.TABLE_DATE_COL（其注释已声明为“写入决策”的单一真源）
# 配合 config 里的 mode 推导:
#   by_date / by_period            → by_year（有明确日期维度的多标的大表）
#   by_code + 有日期列              → by_year（按标的取数，但按年分区存）
#   by_range / by_month / once     → single（区间/一次性汇总，本就不分年）
#   无日期列（如 stock_company）    → by_code（一标一行的静态元数据）
#
# 本函数是**所有表期望布局的单一真源**，供写入路径与回归断言（R9）共用。
# 任何与实际磁盘布局的分歧都会被 R9 拦住。
STORAGE_BY_CODE = "by_code"
STORAGE_BY_YEAR = "by_year"
STORAGE_SINGLE = "single"

# 这些 mode 的产物是区间/月度/一次性汇总，落单文件而非按年分区
# by_param: 元数据全量类（fut_basic/opt_basic），无日期分区维度、无标的文件维度，
#           全量合并为一个文件（总量可控：fut_basic 1.1万 / opt_basic 3.1万行）
# paged:    同为全量元数据类（us_basic/fund_manager/slb_len_mm），逐页拉全后合并
_SINGLE_MODES = ("by_range", "by_month", "once", "by_param", "paged")


def _table_mode(name):
    for src in (BACKFILL_TARGETS, DAILY_UPDATE_EXTRA):
        conf = src.get(name)
        if conf and conf.get("mode"):
            return conf["mode"]
    return None


def table_storage_layout(name):
    """该表的期望存储布局（仅由日期列 + mode 决定，与"怎么取数"解耦）。

    判定顺序（先判"汇总类"，再看有无日期列）:
      1. mode ∈ {by_range, by_month, once, by_param} → single
         （区间/月度/一次性/元数据全量，本就是单文件；
          如 cn_cpi / shibor / index_weight / fut_basic / opt_basic）
      2. 不在 TABLE_DATE_COL 中           → by_code
         （一标一行的静态元数据；全库仅 stock_company）
      3. 其余                             → by_year
    """
    mode = _table_mode(name)
    if mode in _SINGLE_MODES:
        return STORAGE_SINGLE
    if name not in TABLE_DATE_COL:
        return STORAGE_BY_CODE
    return STORAGE_BY_YEAR


def table_date_col(name):
    """该表的分区列；非按年分区表返回 None。"""
    return TABLE_DATE_COL.get(name)


# 逐标的落盘成为真瓶颈的文件大小阈值（MB）
#   判据（2026-09-19 实测）：单次落盘耗时 ≈ 文件大小 × 0.045 ms/KB，
#   而限频 170/min 对应 353ms/次 → 文件 > ~7.8MB 时落盘慢于限频。
#   实证：share_float 2026 文件 8.0MB / 单次 364ms / 消费 165/min < 170，
#         其余 22 个 by_code 表均被限频掩盖（消费 425~103000/min）。
BY_CODE_BATCH_THRESHOLD_MB = 7.5


def by_code_needs_batch(name, dir_path, year=None):
    """判断某 by_code 表是否值得启用批量落盘（预防性优化）。

    返回 True 仅当：该表按年分区存储，且当年文件已超过瓶颈阈值。
    """
    if table_storage_layout(name) != "by_year" or not table_date_col(name):
        return False
    y = year or datetime.now().year
    p = os.path.join(dir_path, f"{y}.parquet")
    if not os.path.exists(p):
        return False
    try:
        return os.path.getsize(p) / 1024 / 1024 > BY_CODE_BATCH_THRESHOLD_MB
    except OSError:
        return False


class ByCodeBatchWriter:
    """by_code 表（按年分区存储）的**批量落盘器**。

    为什么需要它（2026-09-19 实测）:
      by_code 模式下每个标的调用一次 save_df(layout="by_year")，而
      upsert_by_year 每次都对**整个年度文件**做 read→concat→dedup→sort→write，
      成本随文件大小线性增长：
        share_float 2026 文件 8.0MB → 单次 364ms → 5882 标的累计 35.7 分钟
        （而限频下限仅 34.6 分钟）→ 落盘反成瓶颈
      批量后：N 个标的合并为一次写入，总成本从 O(N × 文件大小)
      降为 O(文件大小 + N × 新增行数)。

    ⚠ 适用范围（重要）:
      仅对**超过瓶颈阈值**的表有意义。实测 23 个 by_code 表中 22 个的落盘
      被 limiter.wait() 完全掩盖（消费速率 425~103000/min ≫ 限频 170/min），
      对这些表批量落盘**不会缩短总时长** —— 故由 by_code_needs_batch() 决定是否启用。

    断点纪律（与 YearBatchWriter 一致）:
      on_flushed 在**落盘成功后**触发，调用方应在此回调里标记断点，
      保证"先落盘、后记断点"，崩溃不会造成"断点说完成但数据没写"。
    """

    def __init__(self, dir_path, date_col, subset=None, sort_by=None,
                 flush_codes=500, flush_rows=500_000,
                 on_flushed=None, on_error=None):
        self.dir_path = dir_path
        self.date_col = date_col
        self.subset = subset
        self.sort_by = sort_by
        self.flush_codes = max(int(flush_codes), 1)
        self.flush_rows = max(int(flush_rows), 1)
        self.on_flushed = on_flushed
        self.on_error = on_error
        self._buf = []
        self._keys = []
        self._nrows = 0
        self.flushed_frames = 0
        self.flushed_rows = 0

    def _do_flush(self):
        frames, keys = self._buf, self._keys
        self._buf, self._keys, self._nrows = [], [], 0
        if not frames:
            return
        try:
            merged = pd.concat(frames, ignore_index=True)
            r = upsert_by_year(merged, self.dir_path, date_col=self.date_col,
                               subset=self.subset, sort_by=self.sort_by)
        except Exception as e:
            # 落盘失败 → 不触发 on_flushed（断点不记），下次续跑重拉
            if self.on_error is not None:
                self.on_error(None, e)
            else:
                print(f"  [ERROR] 批量落盘失败: {str(e)[:80]}")
            return
        # ⚠ 2026-09-22 修复（P0）: upsert_by_year 对"某个年份分区写失败"是
        #   **不抛异常、只返回 fail 计数** → 原实现忽略它，部分年份没写进去
        #   却照样触发 on_flushed，断点被标记为完成，数据永不重拉。
        if r.get("fail"):
            msg = f"{r['fail']} 个年份分区写入失败（partitions={r.get('partitions')}）"
            if self.on_error is not None:
                self.on_error(None, RuntimeError(msg))
            else:
                print(f"  [ERROR] 批量落盘部分失败: {msg}")
            return
        self.flushed_frames += 1
        self.flushed_rows += len(merged)
        if self.on_flushed is not None and keys:
            self.on_flushed(keys)

    def add(self, df, key):
        """累积一个标的的数据；达到阈值则自动落盘。"""
        if df is None or len(df) == 0:
            if self.on_flushed is not None:
                self.on_flushed([key])       # 空结果：仍需记断点（三态纪律）
            return
        self._buf.append(df)
        self._keys.append(key)
        self._nrows += len(df)
        if (len(self._keys) >= self.flush_codes
                or self._nrows >= self.flush_rows):
            self._do_flush()

    def flush(self):
        """收尾：落盘剩余缓冲。"""
        self._do_flush()

    def stat(self):
        return {"frames": self.flushed_frames, "rows": self.flushed_rows}


# ============================================================
# 1. 分页拉取
# ============================================================
def fetch_paged_by_date(pro, api_name, limiter, trade_date, verbose=False,
                        date_param="trade_date"):
    """按日期 + offset 分页拉全市场数据（防静默截断）。

    参数:
        date_param: **API 入参名**（默认 "trade_date"）。
                    部分接口用别的名字：fund_div→"ann_date"、eco_cal→"date"。
                    ⚠ 传错会导致参数被静默忽略、返回全表前 N 行（不报错）。

    返回: (df, pages:int, truncated:bool)
      df = None          调用失败 → 调用方不应记断点
      df = 空 DataFrame  调用成功但该日无数据 → 应记断点
      df = 有数据        truncated=True 表示页数触顶, 可能仍不完整
    """
    page, max_pages = BACKFILL_PAGE_CONF.get(api_name, (5000, 10))
    chunks = []
    offset = 0
    pages = 0
    truncated = False
    first_ok = False          # 首次调用是否成功(用于区分 失败 vs 无数据)

    while pages < max_pages:
        params = {date_param: trade_date, "limit": page, "offset": offset}
        df = ts_call_with_retry(pro, api_name, limiter, params, verbose=verbose)
        pages += 1
        if pages == 1:
            first_ok = df is not None
        if df is None or df.empty:
            break
        chunks.append(df)
        if len(df) < page:          # 最后一页
            break
        offset += page
    else:
        truncated = True            # 触及页数上限仍满页

    if not chunks:
        if not first_ok:
            return None, pages, truncated          # 失败
        return pd.DataFrame(), pages, truncated    # 成功但无数据

    out = pd.concat(chunks, ignore_index=True)
    # 分页可能有重叠, 按全列去重
    out = out.drop_duplicates().reset_index(drop=True)
    return out, pages, truncated


# ============================================================
# 2. 落盘
# ============================================================
def _pick_sort_col(df, preferred=None):
    """从已知日期列里挑一个存在的作为排序列"""
    if preferred:
        return preferred
    for c in ("trade_date", "date", "ipo_date", "issue_date", "end_date",
              "ann_date", "month"):
        if c in df.columns:
            return c
    return None


def save_df(target_dir, key, df, layout="single", subset=None, sort_by=None,
            date_col=None):
    """统一落盘。

    参数:
        layout: 存储布局
            "by_year"  按年分区大表 dir/{YYYY}.parquet
                       适用于"按日拉全市场"类(每天数千只标的)。
                       实测比按标的拆文件快 50~100 倍。
            "by_code"  按 ts_code 拆小文件 dir/{ts_code}.parquet
                       适用于"按标的拉全序列"类(查某只股票时命中率高)。
            "single"   单文件 dir/{key}.parquet
                       适用于无 ts_code 的汇总类(margin)或区间类(shibor)。
        subset:  行去重主键。None=默认; "*"=全列判重; ["a","b"]=复合主键
                 ⚠ 单日多行的数据若只给日期列, 同日多行会被判重复而丢失。
        sort_by: 排序列
        date_col: 按年分区的依据列(默认 trade_date)。部分接口无 trade_date
                  (如 disclosure_date 只有 ann_date), 必须显式指定。
    """
    if df is None or df.empty:
        return 0
    ensure_dir(target_dir)

    srt_pref = sort_by or _pick_sort_col(df, date_col)

    # ---- 按年分区 ----
    if layout == "by_year":
        dc = date_col or "trade_date"
        if dc in df.columns:
            # ⚠ 按年大表把全市场塞进同一文件, 去重主键**必须含标的列**。
            #   原来按 ts_code 拆文件时单文件只有一只标的, 用日期列就够;
            #   合并后若仍只用日期列, 同一天的所有标的都会被判为重复而丢失。
            #   这里默认走全列判重, 语义上"完全相同的行才算重复", 绝对安全。
            sub = subset if subset else "*"
            try:
                r = upsert_by_year(df, target_dir, date_col=dc,
                                   subset=sub, sort_by=srt_pref)
            except Exception as e:
                print(f"  [WARN] 按年分区失败, 回退单文件: {str(e)[:70]}")
                layout = "single"
            else:
                # ⚠ 2026-09-22 修复（P0）: upsert_by_year 对"个别年份分区写失败"
                #   不抛异常、只返回 fail 计数 → 原实现直接 return 成功，
                #   调用方据此记断点，导致该批数据永不重拉。
                #   fail>0 时**不回退单文件**（回退会在年度目录里写出 {api}.parquet
                #   造成混合布局），直接抛出让调用方跳过断点标记。
                if r.get("fail"):
                    raise RuntimeError(
                        f"按年分区 {r['fail']} 个年份写入失败"
                        f"（partitions={r.get('partitions')}），"
                        f"未回退单文件（避免混合布局）")
                return r["partitions"]
        else:
            layout = "single"       # 无分区列, 回退

    # ---- 按标的拆分 ----
    if layout == "by_code" and "ts_code" in df.columns and subset != "*":
        sub = subset or ["trade_date"]
        if not isinstance(sub, list):
            sub = [sub]
        srt = srt_pref or (sub[0] if sub else "trade_date")
        if srt not in df.columns:
            srt = sub[0] if sub else None
        stat = upsert_grouped(df, target_dir, key_col="ts_code",
                              subset=sub, sort_by=srt)
        return stat["processed"]

    # ---- 单文件 ----
    path = os.path.join(target_dir, f"{key}.parquet")
    sub = subset if subset else "trade_date"
    srt = srt_pref
    if srt is None and sub != "*":
        srt = sub[0] if isinstance(sub, list) else sub
    if srt is not None and srt not in df.columns:
        srt = None
    merge_append(path, df, subset=sub, sort_by=srt)
    return 1


# 兼容旧名
_save_df = save_df


# ============================================================
# 3. 前置基础表
# ============================================================
def ensure_meta(pro, limiter, kind, dry_run=False):
    """按需拉取前置基础表 (fx_obasic / hk_basic), 缓存到 metadata。

    返回 True 表示已就绪（已存在或拉取成功）。
    """
    api = {"fx": "fx_obasic", "hk": "hk_basic"}.get(kind)
    if api is None:
        return False
    path = os.path.join(META_DIR, f"{api}.parquet")
    if os.path.exists(path):
        return True
    if dry_run:
        print(f"  [前置] 需要 {api}(dry-run 跳过拉取)")
        return False
    print(f"  [前置] 拉取 {api} ...")
    df = ts_call_with_retry(pro, api, limiter, {})
    if df is not None and not df.empty:
        ensure_dir(META_DIR)
        df.to_parquet(path, index=False)
        print(f"  [前置] {api}: {len(df)} 行已保存")
        return True
    print(f"  [前置] {api} 拉取失败")
    return False


# 兼容旧名
_ensure_meta = ensure_meta
