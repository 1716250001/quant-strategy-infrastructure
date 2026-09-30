# -*- coding: utf-8 -*-
"""
common/reader.py — 全量库统一读取层（布局无关）
================================================
为什么需要它
------------
全量库存在三种存储布局，下游若各自硬编码路径，一旦布局调整就要全量改代码：
    by_code   {table}/{ts_code}.parquet        一标的一文件
    by_year   {table}/{YYYY}.parquet           一年一文件
    single    {table}/{key}.parquet            单文件

本模块提供**布局无关**的读取接口：调用方只说"要哪张表、哪个标的、哪段区间"，
由本层自动适配当前布局。布局调整时**下游零改动**。

设计要点
--------
1. 布局自动探测 + 结果缓存（避免重复扫目录）
2. 兼容两种布局共存 —— 迁移可渐进执行，中途随时可用/可回滚
3. 读单标的时用 parquet 谓词下推（filters），避免全文件载入后再筛
4. 代码清单优先取元数据表（含退市股），不依赖"遍历文件名"

用法
----
    from common import reader

    # 取代码清单
    codes = reader.list_codes("daily")                  # 含退市
    codes = reader.list_codes("daily", listed_only=True) # 仅在市

    # 读单标的（自动适配布局）
    df = reader.read_code("daily", "600519.SH")
    df = reader.read_code("fund_daily", "510050.SH",
                          columns=["trade_date", "close"],
                          start_date="20260101", end_date="20260917")

    # 读区间全市场（用于截面扫描）
    df = reader.read_range("daily", start_date="20260901", end_date="20260917")

    # 迭代年度文件（需要自己控制读取时）
    for year, path in reader.iter_year_files("moneyflow"):
        ...
"""
import os
from datetime import datetime

import pandas as pd

from config import MARKET_DATA_DIR, META_DIR
from common.parquet_store import list_parquet_files

# 布局探测结果缓存 {(root, table): layout}
_LAYOUT_CACHE = {}

# 代码清单缓存 {(root, table, listed_only, from_store): codes}
#   ⚠ 写入后必须失效 —— parquet_store 的写函数已接入（见 _invalidate_reader_cache）
_CODES_CACHE = {}

# 各表「代码清单」的元数据来源
# by_year 布局下无法再从文件名取代码，改为读元数据表（含退市，更完整）
META_SOURCE = {
    "daily":          ("stock_basic.parquet", "ts_code", None),
    "daily_basic":    ("stock_basic.parquet", "ts_code", None),
    "adj_factor":     ("stock_basic.parquet", "ts_code", None),
    "stk_limit":      ("stock_basic.parquet", "ts_code", None),
    "moneyflow":      ("stock_basic.parquet", "ts_code", None),
    "margin_detail":  ("stock_basic.parquet", "ts_code", None),
    "fund_daily":     ("fund_basic.parquet",  "ts_code", None),
    "fund_adj":       ("fund_basic.parquet",  "ts_code", None),
    "fund_share":     ("fund_basic.parquet",  "ts_code", None),
    "fund_nav":       ("fund_basic.parquet",  "ts_code", None),
    "cb_daily":       ("cb_basic.parquet",    "ts_code", None),
    "index_daily":    ("index_basic.parquet", "ts_code", None),
    "index_dailybasic": ("index_basic.parquet", "ts_code", None),
    "fx_daily":       ("fx_obasic.parquet",   "ts_code", None),
    "hk_hold":        ("hk_basic.parquet",    "ts_code", None),
}

# 「在市」判定列（不同元数据表列名不同）
LISTED_COL = {
    "stock_basic.parquet": ("list_status", "L"),
    "fund_basic.parquet":  ("market", "E"),      # 场内才有行情
    "cb_basic.parquet":    ("delist_date", None),  # 空值=在市
    "index_basic.parquet": (None, None),
    "fx_obasic.parquet":   (None, None),
    "hk_basic.parquet":    (None, None),
}


# ============================================================
# 布局探测
# ============================================================
def table_dir(table, root=None):
    """表目录（不保证存在）

    root 为库根，None 则用 config.MARKET_DATA_DIR。
    部分工具（fund_pool_builder / build_micro_index）带 data_dir 参数，
    需要指定库根，故这里支持显式传入。
    """
    return os.path.join(root or MARKET_DATA_DIR, table)


def _meta_dir(root=None):
    return os.path.join(root or MARKET_DATA_DIR, "metadata")


def _cache_key(table, root=None):
    return (root or MARKET_DATA_DIR, table)


def detect_layout(table, refresh=False, root=None):
    """探测表的存储布局。返回 'by_code' / 'by_year' / 'single' / 'none'。

    - by_year: 文件名形如 2026.parquet
    - by_code: 文件名形如 600519.SH.parquet
    - single : 其他（含单文件表）
    - none   : 目录不存在或无 parquet
    """
    key = _cache_key(table, root)
    if not refresh and key in _LAYOUT_CACHE:
        return _LAYOUT_CACHE[key]

    d = table_dir(table, root)
    files = list_parquet_files(d)
    if not files:
        _LAYOUT_CACHE[key] = "none"
        return "none"

    year_like = [f for f in files if len(f) == 12 and f[:4].isdigit()]
    if len(year_like) == len(files):
        layout = "by_year"
    elif any("." in f[:-8] for f in files):     # xxx.SH.parquet
        layout = "by_code"
    else:
        layout = "single"

    _LAYOUT_CACHE[key] = layout
    return layout


def clear_cache(table=None, root=None):
    """清空布局/代码清单缓存。

    调用时机:
      - 迁移或写入数据后（parquet_store 的写函数已自动接入）
      - 测试时重置状态

    参数:
        table: 只清该表；None 则清空全部
        root:  库根

    ⚠ root 的语义（易错）:
        root=None 时**清该表在所有 root 下的缓存**，而非只清默认根。
        原因：写入方（parquet_store）只知道路径、不知道调用方的 root，
        若只清默认根，带 root 参数的调用方（如 alt 库、测试用临时目录）
        会读到陈旧值 —— 实证该场景曾导致缓存失效静默失败。
    """
    if not table:
        _LAYOUT_CACHE.clear()
        _CODES_CACHE.clear()
        return

    if root is not None:
        r = root
        _LAYOUT_CACHE.pop(_cache_key(table, r), None)
        for k in [k for k in _CODES_CACHE if k[0] == r and k[1] == table]:
            _CODES_CACHE.pop(k, None)
        return

    # root 未指定 → 清该表在所有 root 下的缓存
    for k in [k for k in list(_LAYOUT_CACHE) if k[1] == table]:
        _LAYOUT_CACHE.pop(k, None)
    for k in [k for k in list(_CODES_CACHE) if k[1] == table]:
        _CODES_CACHE.pop(k, None)


def inspect_layout(table, root=None):
    """布局详情（含**混合布局**检测），供"如实报告"类工具使用。

    与 detect_layout 的分工:
      detect_layout   → 返回单一标签，供**读取路径分支**用（by_year/by_code/single）
      inspect_layout  → 在标签之外给出文件构成，能识别"年度文件 + 其他文件并存"
                        的异常状态（此时 detect_layout 会给出误导性的 single/by_code）

    ⚠ 为什么需要它:
      实证 margin 目录曾有 margin.parquet(旧单文件) + 2026.parquet(年度文件) 并存，
      detect_layout 的两条判定都不满足 → 落 single 分支 → 只读旧单文件，
      年度文件里的新数据成为静默黑洞。报告工具必须能看出这种状态。

    返回 dict:
        layout      主判定（与 detect_layout 一致）
        display     报告用标签（混合布局时为 "mixed"）
        npart       分区数（by_year=年份数 / by_code=代码数 / single=1）
        is_mixed    是否存在"年度文件 + 非年度文件"并存
        year_files / code_files / other_files   各类型文件数
        files       文件总数
    """
    d = table_dir(table, root)
    files = list_parquet_files(d)
    if not files:
        return {"layout": "none", "display": "none", "npart": 0,
                "is_mixed": False, "year_files": 0, "code_files": 0,
                "other_files": 0, "files": 0}

    year_like = [f for f in files if len(f) == 12 and f[:4].isdigit()]
    code_like = [f for f in files if "." in f[:-8]]
    other = [f for f in files if f not in year_like and f not in code_like]
    is_mixed = bool(year_like) and len(year_like) != len(files)

    layout = detect_layout(table, root=root)
    if layout == "by_year":
        npart = len(year_like)
    elif layout == "by_code":
        npart = len(code_like)
    else:
        npart = len(files)

    return {
        "layout": layout,
        "display": "mixed" if is_mixed else layout,
        "npart": npart,
        "is_mixed": is_mixed,
        "year_files": len(year_like),
        "code_files": len(code_like),
        "other_files": len(other),
        "files": len(files),
    }


# ============================================================
# 代码清单
# ============================================================
def list_codes(table, listed_only=False, root=None):
    """返回该表的证券代码清单。

    by_year → 从元数据表读取（含退市，比遍历文件完整）
    by_code → 从文件名提取
    其他    → 返回 []

    参数:
        listed_only: True 则只返回在市标的（无元数据的表忽略此参数）

    性能: 结果带缓存（key 含 listed_only）。
          ⚠ 写入数据后需失效缓存 —— parquet_store 的写函数已自动接入。
    """
    ck = (root or MARKET_DATA_DIR, table, bool(listed_only), False)
    if ck in _CODES_CACHE:
        return _CODES_CACHE[ck]

    codes = _list_codes_uncached(table, listed_only, root)
    _CODES_CACHE[ck] = codes
    return codes


def _list_codes_uncached(table, listed_only=False, root=None):
    """list_codes 的实际实现（无缓存）"""
    layout = detect_layout(table, root=root)

    if layout == "by_code":
        codes = [f[:-8] for f in list_parquet_files(table_dir(table, root))]
        return sorted(codes)

    if layout == "none":
        return []

    # by_year / single → 走元数据
    src = META_SOURCE.get(table)
    if not src:
        # 无元数据映射 → 退回"从年度文件提取 ts_code"
        return _codes_from_year_files(table, listed_only, root)

    meta_file, code_col, _ = src
    meta_path = os.path.join(_meta_dir(root), meta_file)
    if not os.path.exists(meta_path):
        return _codes_from_year_files(table, listed_only, root)

    md = pd.read_parquet(meta_path)
    if code_col not in md.columns:
        return _codes_from_year_files(table, listed_only, root)

    if listed_only:
        col, val = LISTED_COL.get(meta_file, (None, None))
        if col and col in md.columns:
            if val is None:
                # delist_date 型: 空值=在市
                m = md[col].isna() | (md[col] == "")
                md = md[m]
            else:
                md = md[md[col] == val]
    return sorted(md[code_col].dropna().unique().tolist())


def _codes_from_year_files(table, listed_only=False, root=None):
    """兜底：从年度文件里提取 ts_code 唯一值（只读该列，较快）"""
    codes = set()
    for _, path in iter_year_files(table, root=root):
        try:
            s = pd.read_parquet(path, columns=["ts_code"])["ts_code"]
            codes.update(s.dropna().unique().tolist())
        except Exception:
            pass
    return sorted(codes)


def codes_from_store(table, root=None):
    """库内**实际存在数据**的代码全集。

    与 list_codes 的差别：list_codes 在 by_year 布局下优先读元数据表
    （含从未入库的标的）；本函数只反映"库里真的有数据"，用于覆盖率、
    "是否下载过"这类判定。

    性能: 结果带缓存。by_year 布局下需扫**全部年度文件**的 ts_code 列
          （实测 daily 37 文件 0.63s / moneyflow 11 文件 0.46s），
          而本函数被 7 处调用（fund_pool_builder / check_coverage /
          export_lof / list_lof / full_download / cb_download / backfill）。
          ⚠ 写入数据后需失效缓存 —— parquet_store 的写函数已自动接入。
    """
    ck = (root or MARKET_DATA_DIR, table, False, True)
    if ck in _CODES_CACHE:
        return _CODES_CACHE[ck]

    layout = detect_layout(table, root=root)
    if layout == "none":
        codes = []
    elif layout == "by_code":
        codes = sorted(f[:-8] for f in list_parquet_files(table_dir(table, root)))
    else:
        codes = _codes_from_year_files(table, root=root)

    _CODES_CACHE[ck] = codes
    return codes


def iter_year_files(table, root=None):
    """迭代 (年份, 完整路径)。仅对 by_year 布局有效。"""
    if detect_layout(table, root=root) != "by_year":
        return
    for f in list_parquet_files(table_dir(table, root), sort=True):
        yield f[:4], os.path.join(table_dir(table, root), f)


# ============================================================
# 读取
# ============================================================
def _filters(code=None, code_col="ts_code", start_date=None, end_date=None,
             date_col="trade_date"):
    """构造 parquet 谓词下推过滤器（减少读入量）"""
    flt = []
    if code is not None:
        flt.append((code_col, "==", code))
    if start_date is not None:
        flt.append((date_col, ">=", str(start_date)))
    if end_date is not None:
        flt.append((date_col, "<=", str(end_date)))
    return flt or None


def _read_one(path, columns=None, flt=None):
    """读单个 parquet，带 filters 兜底（老 pandas 无 filters 时退化为读后筛）

    ⚠ 为什么 filters 失败要降级而不是直接返回空表:
      parquet 谓词下推要求 flt 里的列**存在于文件 schema 中**，否则 pyarrow
      整体报错。而调用方常按默认 date_col="trade_date" 传区间过滤，遇到
      cb_issue / fund_adj 这类"没有 trade_date 列"的表就会命中该错误。
      若在此直接返回空表，会被误读成"这只标的那段区间确实没数据"——
      静默丢数据比报错更危险。故降级为"读全列 + 内存筛选"。
    """
    try:
        return pd.read_parquet(path, columns=columns, filters=flt)
    except TypeError:
        return _read_one_manual(path, columns, flt)
    except Exception:
        if flt:
            try:
                return _read_one_manual(path, columns, flt)
            except Exception:
                return pd.DataFrame()
        return pd.DataFrame()


def _read_one_manual(path, columns=None, flt=None):
    """不带谓词下推的读取（读后在内存里筛选）"""
    df = pd.read_parquet(path, columns=columns)
    if flt:
        for col, op, val in flt:
            if col not in df.columns:
                continue
            if op == "==":
                df = df[df[col] == val]
            elif op == ">=":
                df = df[df[col] >= val]
            elif op == "<=":
                df = df[df[col] <= val]
    return df


def read_code(table, code, columns=None, start_date=None, end_date=None,
              date_col="trade_date", code_col="ts_code", root=None):
    """读单个标的的数据（布局无关）。

    参数:
        table:      表名（目录名）
        code:       ts_code，如 "600519.SH"
        columns:    需要的列（None=全部）
        start_date: 起始日期 YYYYMMDD（含）
        end_date:   结束日期 YYYYMMDD（含）
        date_col:   日期列名（默认 trade_date）
        code_col:   代码列名（默认 ts_code）
        root:       库根（None=config.MARKET_DATA_DIR）
    返回:
        DataFrame（按日期升序；无数据返回空 DataFrame）
    """
    layout = detect_layout(table, root=root)
    d = table_dir(table, root)

    if layout == "none":
        return pd.DataFrame()

    if layout == "by_code":
        path = os.path.join(d, f"{code}.parquet")
        if not os.path.exists(path):
            return pd.DataFrame()
        flt = _filters(None, start_date=start_date, end_date=end_date,
                       date_col=date_col)
        df = _read_one(path, columns=columns, flt=flt)
        return _sort(df, date_col)

    if layout == "single":
        # 单文件表：读后筛代码
        df = _read_one(os.path.join(d, f"{table}.parquet"), columns=columns)
        if df.empty:
            return df
        if code_col in df.columns:
            df = df[df[code_col] == code]
            if start_date is not None and date_col in df.columns:
                df = df[df[date_col] >= str(start_date)]
            if end_date is not None and date_col in df.columns:
                df = df[df[date_col] <= str(end_date)]
        return _sort(df, date_col)

    # by_year：按年份逐个读，谓词下推到行组
    flt = _filters(code, code_col=code_col,
                   start_date=start_date, end_date=end_date, date_col=date_col)
    parts = []
    for year, path in iter_year_files(table, root=root):
        if start_date is not None and year < str(start_date)[:4]:
            continue
        if end_date is not None and year > str(end_date)[:4]:
            continue
        one = _read_one(path, columns=columns, flt=flt)
        if not one.empty:
            parts.append(one)
    if not parts:
        return pd.DataFrame()
    return _sort(pd.concat(parts, ignore_index=True), date_col)


def read_range(table, columns=None, start_date=None, end_date=None,
               date_col="trade_date", root=None):
    """读某段日期的全市场数据（用于截面扫描 / 区间回测）。

    by_year 布局下这是**顺序读年度文件**，比遍历数千小文件快得多。
    """
    layout = detect_layout(table, root=root)
    d = table_dir(table, root)

    if layout == "none":
        return pd.DataFrame()

    if layout == "by_year":
        flt = _filters(None, start_date=start_date, end_date=end_date,
                       date_col=date_col)
        parts = []
        for year, path in iter_year_files(table, root=root):
            if start_date is not None and year < str(start_date)[:4]:
                continue
            if end_date is not None and year > str(end_date)[:4]:
                continue
            one = _read_one(path, columns=columns, flt=flt)
            if not one.empty:
                parts.append(one)
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    if layout == "by_code":
        parts = []
        for f in list_parquet_files(d):
            one = _read_one(os.path.join(d, f), columns=columns)
            if not one.empty:
                if start_date is not None and date_col in one.columns:
                    one = one[one[date_col] >= str(start_date)]
                if end_date is not None and date_col in one.columns:
                    one = one[one[date_col] <= str(end_date)]
                if not one.empty:
                    parts.append(one)
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    # single
    df = _read_one(os.path.join(d, f"{table}.parquet"), columns=columns)
    if df.empty:
        return df
    if start_date is not None and date_col in df.columns:
        df = df[df[date_col] >= str(start_date)]
    if end_date is not None and date_col in df.columns:
        df = df[df[date_col] <= str(end_date)]
    return df


def _sort(df, date_col):
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df
    if date_col in df.columns:
        df = df.sort_values(date_col).reset_index(drop=True)
    return df


# ============================================================
# 便捷封装（供明确知道表含义的场景）
# ============================================================
def read_stock_daily(code, **kw):
    """个股日线"""
    return read_code("daily", code, **kw)


def read_fund_daily(code, **kw):
    """ETF/LOF 日线"""
    return read_code("fund_daily", code, **kw)


def read_index_daily(code, **kw):
    """指数日线"""
    return read_code("index_daily", code, **kw)


def read_cb_daily(code, **kw):
    """可转债日线"""
    return read_code("cb_daily", code, **kw)


def latest_date(table, date_col="trade_date", root=None):
    """该表的最新日期。

    by_year → 读最后一个年度文件（快）
    by_code → 优先用 config.GAP_UPDATE_SAMPLE_CODES 里为该表配置的采样标的
              （避免抽到已退市标的，如 fund_daily 里的分级基金 150xxx）
    """
    layout = detect_layout(table, root=root)
    d = table_dir(table, root)
    if layout == "none":
        return None

    if layout == "by_year":
        files = list_parquet_files(d, sort=True)
        for f in reversed(files):
            try:
                s = pd.read_parquet(os.path.join(d, f), columns=[date_col])[date_col]
                if len(s):
                    return str(s.max())
            except Exception:
                continue
        return None

    # by_code / single：优先采样标的
    try:
        from config import GAP_UPDATE_SAMPLE_CODES
        for code in GAP_UPDATE_SAMPLE_CODES.get(table, []):
            p = os.path.join(d, f"{code}.parquet")
            if os.path.exists(p):
                try:
                    s = pd.read_parquet(p, columns=[date_col])[date_col]
                    if len(s):
                        return str(s.max())
                except Exception:
                    continue
    except Exception:
        pass

    # 兜底：多抽一些文件取最大值（而非第一个命中）
    best = None
    for f in list_parquet_files(d)[:200]:
        try:
            s = pd.read_parquet(os.path.join(d, f), columns=[date_col])[date_col]
            if len(s):
                mx = str(s.max())
                if best is None or mx > best:
                    best = mx
        except Exception:
            continue
    return best
