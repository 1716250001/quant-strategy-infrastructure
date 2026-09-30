# -*- coding: utf-8 -*-
"""Parquet 布局统一读取器：by_year / single / metadata 三布局 + 谓词下推（PoC-1 任务 1.2/1.4）。

设计依据（05 §9.1 适配层 + 现有 common/reader.py 语义重实现）：
    - 三种布局对外统一为「表 → 行批」接口，调用方不感知物理布局；
    - 谓词下推：日期区间（str 比较即可，YYYYMMDD 字典序=时间序）+ 列裁剪；
    - 单位换算（1.4 契约）：在 `_apply_conversions` 单点落地——
      消费侧不再各自换算（评审 C1 数据约束对策）。

已知陷阱（沿用现有工程文档纪律）：
    - 旧写法 glob(dir/*.parquet) 拿到的是年份列表而非数据——本读取器按布局
      显式解析，by_year 目录中的非 YYYY.parquet 文件直接报错。
"""
from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.compute as pc
import pyarrow.parquet as pq

from btf.data.tables_meta import TABLES, Layout, TableMeta

_YEAR_RE = re.compile(r"^\d{4}\.parquet$")


class ReaderError(Exception):
    """读取层错误（布局违例/表未登记/文件缺失）。"""


def _read(path: Path, /, **kwargs):
    """`pq.read_table` 统一入口（IO-9）：对**主库**启用 `memory_map`。

    为什么只对主库启用：`memory_map=True` 会保持文件映射——Windows 下被映射
    的文件**不可删除/覆盖**。主库（`MARKET_DATA_DIR`）是只读原始数据，映射安全
    且省一次内核→用户态拷贝（大表收益随行数放大）；测试夹具写在 tmp 目录
    且会被删除，故按路径判定，非主库走普通读（行为与旧版逐字一致）。
    """
    from btf.config.paths import MARKET_DATA_DIR

    try:
        in_master = path.resolve().is_relative_to(Path(MARKET_DATA_DIR).resolve())
    except OSError:                                  # pragma: no cover
        in_master = False
    if in_master:
        kwargs.setdefault("memory_map", True)
    return pq.read_table(path, **kwargs)


def _concat_unified(parts: list) -> object:
    """schema 容忍拼接（PoC-3 实测：dividend 1990-1992 年文件由旧管道
    导出，large_string/null 列类型与后续年不一致，严格 concat 报错）。

    统一规则（无损最小集）：
        - 全部 schema 一致 → 直接 concat（B1/B2 热路径零开销）；
        - null ↔ 任意类型 → 取具体类型（null 列 cast 全 null 安全）；
        - large_string ↔ string → 统一 string（语义等价，cast 无损）；
        - 其它类型冲突 → ReaderError（拒绝静默数据变形）。
    """
    import pyarrow as pa

    if len(parts) == 1:
        return parts[0]
    schemas = {p.schema for p in parts}
    if len(schemas) == 1:
        return pa.concat_tables(parts)
    base = parts[0].schema
    names = base.names
    types = [base.field(i).type for i in range(len(names))]
    for p in parts[1:]:
        for i, f in enumerate(p.schema):
            cur, other = types[i], f.type
            if cur == other:
                continue
            if pa.types.is_null(cur):
                types[i] = other
            elif pa.types.is_null(other):
                continue
            elif {str(cur), str(other)} == {"string", "large_string"}:
                types[i] = pa.string()          # 互通：统一 string（cast 无损）
            else:
                raise ReaderError(
                    f"年文件列类型不可统一: {names[i]} {cur} vs {other}"
                    "（读取器只容忍 null/large_string 异构）")
    target = pa.schema([pa.field(n, t) for n, t in zip(names, types, strict=True)])
    return pa.concat_tables([p.cast(target) for p in parts])


def _resolve_root(meta: TableMeta, root: Path) -> Path:
    if meta.layout is Layout.METADATA:
        return root / "metadata"
    return root / meta.name


def _list_year_files(meta: TableMeta, root: Path) -> list[tuple[int, Path]]:
    """by_year 布局的 (year, path) 升序列表；非年份文件即报错（防静默漏读）。"""
    directory = _resolve_root(meta, root)
    if not directory.is_dir():
        raise ReaderError(f"表目录缺失: {directory}")
    out: list[tuple[int, Path]] = []
    for p in sorted(directory.glob("*.parquet")):
        m = _YEAR_RE.match(p.name)
        if not m:
            raise ReaderError(f"by_year 布局含非年份文件 {p}（布局违例，拒绝静默跳过）")
        out.append((int(p.stem), p))
    return out


def read_full(
    table: str,
    root: Path,
    *,
    columns: Sequence[str] | None = None,
    years: Sequence[int] | None = None,
) -> object:
    """全表读取（single/metadata 布局主用；by_year 亦可但慎用——全量内存）。

    `years`（IO-3/P1-8）：仅读指定年份文件（by_year 布局）——**调用方负责
    证明超集性**：如 `dividend` 按**方案年度**归档（ex_date 可晚于文件年
    最多 5 年，实测分布见 `feed.corporate_actions`），须加回看窗口。
    """
    import pyarrow as pa

    meta = TABLES.get(table)
    if meta is None:
        raise ReaderError(f"表未登记: {table}（17 表注册表见 tables_meta）")
    if meta.layout is Layout.BY_YEAR:
        wanted = None if years is None else {int(y) for y in years}
        parts = [
            _read(p, columns=list(columns) if columns else None)
            for year, p in _list_year_files(meta, root)
            if wanted is None or year in wanted
        ]

        return _concat_unified(parts) if parts else pa.table({})
    path = _resolve_root(meta, root) / f"{table}.parquet"
    if not path.is_file():
        raise ReaderError(f"单文件缺失: {path}")
    return _read(path, columns=list(columns) if columns else None)


def read_range(
    table: str,
    root: Path,
    start_ymd: str,
    end_ymd: str,
    *,
    columns: Sequence[str] | None = None,
) -> object:
    """日期区间读取（谓词下推：布局内先按年份文件裁剪，再按日期列过滤）。

    仅适用于 date_col 语义为「逐日记录」的表（daily/adj_factor/stk_limit/
    moneyflow/fund_daily/etf_limit/index_daily/index_dailybasic/suspend_d 等）；
    区间型表（namechange/dividend/fina_indicator）用 read_full + 自行区间判定。
    """
    meta = TABLES.get(table)
    if meta is None:
        raise ReaderError(f"表未登记: {table}")
    if meta.date_col is None:
        raise ReaderError(f"表 {table} 无日期列（静态表），用 read_full")

    date_col = meta.date_col
    wanted = list(columns) if columns is not None else None

    import pyarrow as pa

    parts: list[pa.Table] = []
    if meta.layout is Layout.BY_YEAR:
        y0, y1 = int(start_ymd[:4]), int(end_ymd[:4])
        for year, path in _list_year_files(meta, root):
            if year < y0 or year > y1:
                continue
            t = _read(path, columns=wanted)
            # 文件级裁剪：整年在区间内则跳过行过滤（快路径）
            if year > y0 and year < y1:
                parts.append(t)
                continue
            mask = pc.and_(
                pc.greater_equal(t.column(date_col), start_ymd),
                pc.less_equal(t.column(date_col), end_ymd),
            )
            t = t.filter(mask)
            if t.num_rows:
                parts.append(t)
    else:
        t = read_full(table, root, columns=wanted)
        mask = pc.and_(
            pc.greater_equal(t.column(date_col), start_ymd),
            pc.less_equal(t.column(date_col), end_ymd),
        )
        t = t.filter(mask)
        if t.num_rows:
            parts.append(t)


    if not parts:
        # 空结果：以列名构造空表（保持 schema 可预期；调用方按 num_rows==0 处理）
        schema_cols = wanted or peek_columns(table, root)
        return pa.table({c: pa.array([], type=pa.string()) if c in (date_col, "ts_code")
                         else pa.array([], type=pa.float64()) for c in schema_cols})
    return _concat_unified(parts)


def peek_columns(table: str, root: Path) -> list[str]:
    """列名探测（读任一文件的 schema，零数据代价）。

    公开理由（M1 任务 4.1 实证教训）：调用方按"想要的列"向 read_full
    传 columns 时，列在物理 schema 缺失会令 pyarrow Scanner 直接抛
    ArrowInvalid（stock_basic 无 market 列实测）——请求前先经本函数
    求交集，缺失列交由调用方语义回退（如板块按代码前缀推断）。
    """
    meta = TABLES[table]
    if meta.layout is Layout.BY_YEAR:
        files = _list_year_files(meta, root)
        if files:
            return pq.read_schema(files[0][1]).names
    path = _resolve_root(meta, root) / f"{table}.parquet"
    if path.is_file():
        return pq.read_schema(path).names
    return []


def read_codes(
    table: str,
    root: Path,
    symbols: Sequence[str],
    start_ymd: str,
    end_ymd: str,
    *,
    columns: Sequence[str] | None = None,
) -> object:
    """多标的区间读取（谓词下推：ts_code IN (...) AND 日期区间）。"""
    meta = TABLES.get(table)
    if meta is None:
        raise ReaderError(f"表未登记: {table}")
    date_col = meta.date_col
    wanted = list(columns) if columns is not None else None

    import pyarrow as pa

    symbol_set = list(set(symbols))
    parts: list[pa.Table] = []
    if meta.layout is Layout.BY_YEAR:
        y0, y1 = int(start_ymd[:4]), int(end_ymd[:4])
        for year, path in _list_year_files(meta, root):
            if year < y0 or year > y1:
                continue
            t = _read(path, columns=wanted)
            mask = pc.is_in(t.column("ts_code"), value_set=pa.array(symbol_set))
            if year == y0 or year == y1:
                date_mask = pc.and_(
                    pc.greater_equal(t.column(date_col), start_ymd),
                    pc.less_equal(t.column(date_col), end_ymd),
                )
                mask = pc.and_(mask, date_mask)
            t = t.filter(mask)
            if t.num_rows:
                parts.append(t)
    else:
        t = read_full(table, root, columns=wanted)
        mask = pc.is_in(t.column("ts_code"), value_set=pa.array(symbol_set))
        date_mask = pc.and_(
            pc.greater_equal(t.column(date_col), start_ymd),
            pc.less_equal(t.column(date_col), end_ymd),
        )
        t = t.filter(pc.and_(mask, date_mask))
        if t.num_rows:
            parts.append(t)

    if not parts:
        return pa.table({})
    return _concat_unified(parts)


def iter_day_slices(
    table: str,
    root: Path,
    start_ymd: str,
    end_ymd: str,
    *,
    columns: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
) -> Iterator[tuple[str, dict[str, list], int, int]]:
    """引擎热路径：按年流式 + 列式行区间（PoC-1 B1 重构，CP1 剖析结论落地）。

    yield (ymd, cols, lo, hi)：
        ymd  —— 交易日（YYYYMMDD，升序）
        cols —— **该年**的列式数据（{列名: list}，原始口径；单位换算在 Feed 层）
        lo/hi —— 该日在 cols 中的行区间 [lo, hi)

    symbols（v0.5 V5-3 B4）：**标的谓词下推**——read 后 Arrow `is_in`
    过滤再排序/pylist。子集宇宙场景（B2/B4 ~20 标的）行数从 ~170 万/年
    降至 ~5 千/年，sort+pylist 成本近零；None = 全市场（B3 语义不变）。

    性能设计（B1 剖析证据 2026-09-26）：
        - Arrow I/O+列裁剪 10 年仅 0.70s（谓词下推不是瓶颈）；
        - 瓶颈是 950 万行的 Python 对象化（逐行 dict ~50s + Bar 构造 ~16s）；
        - 故本函数**不做任何逐行 Python 对象化**——按年排序列分组，行区间
          交由消费方按需懒转换（事件循环当日只消费 ~4000 Bar，成本分摊进 B3）。
        - B1 余量回收（同日二次剖析，成本分解 I/O 0.60 / 双键排序 4.34 /
          pylist 3.03 / while 分组 1.02s）：
          ① ts_code 全局升序的年文件（主库 66 文件中 65 个实证，唯一例外
             daily/2026）单键 date 稳定排序与双键 (date, ts_code) **逐行
             等价**（probe_sort_equiv 全等验证，Arrow sort 稳定）——快路径
             跳过次键 4.34s→2.86s，升序守卫仅 0.16s，非升序回退双键；
          ② dates 排序后用 bisect_right（C 实现，O(log n)/日）找日界，
             替代 950 万次 Python 逐行比较 1.02s→≈0。
        - 确定性：年内按 (date, ts_code) 升序排序，同参数输出稳定。
    """
    meta = TABLES.get(table)
    if meta is None:
        raise ReaderError(f"表未登记: {table}")
    date_col = meta.date_col
    if date_col is None:
        raise ReaderError(f"表 {table} 无日期列（静态表）")
    wanted = list(columns) if columns is not None else None
    subset: list[str] | None = (
        sorted(set(symbols)) if symbols is not None else None)

    y0, y1 = int(start_ymd[:4]), int(end_ymd[:4])
    for year, path in _list_year_files(meta, root):
        if year < y0 or year > y1:
            continue
        t = _read(path, columns=wanted)
        if t.num_rows == 0:
            continue
        if subset is not None and "ts_code" in t.column_names:
            import pyarrow as pa

            t = t.filter(pc.is_in(
                t.column("ts_code"),
                value_set=pa.array(subset,
                                   type=t.schema.field("ts_code").type)))
            if t.num_rows == 0:
                continue
        # 快路径（docstring 证据 ①）：ts_code 全局升序 → 稳定单键排序与双键逐行等价
        ts = t.column("ts_code")
        m = ts.length()
        ts_sorted = m <= 1 or bool(
            pc.all(pc.less_equal(ts.slice(0, m - 1), ts.slice(1))).as_py()
        )
        keys = (
            [(date_col, "ascending")]
            if ts_sorted
            else [(date_col, "ascending"), ("ts_code", "ascending")]
        )
        t = t.sort_by(keys)
        cols = {n: t.column(n).to_pylist() for n in t.column_names}
        dates = cols[date_col]
        # 分组（docstring 证据 ②）：dates 已升序 → bisect_right 找日界
        i = 0
        n = len(dates)
        while i < n:
            day = dates[i]
            j = bisect_right(dates, day, i + 1, n)
            if start_ymd <= day <= end_ymd:
                yield day, cols, i, j
            i = j


# ─────────────────────────────────────────────────────────────
# EX-4 完全体（19 号 §9.2 / §49）：**磁盘知识**全部收拢在本模块与 `core`
# ─────────────────────────────────────────────────────────────
# 下列四个公开函数是「布局解析 + 文件级读取 + 元数据」的**唯一实现**；
# `core.YearTableStore` 逐一同名委托，`data` 域其余模块**只经 core**。
# 契约新 9 已强化为「仅 `btf.data.core` 可 import 本模块」，并由
# `tools/check_io_boundary.py`（`bt check` 第 9 项）机器守卫。


@dataclass(frozen=True)
class FileMeta:
    """单文件元数据（版本指纹**元数据档**的唯一取数口；不含数据页）。"""

    stem: str
    size: int
    mtime_ns: int
    num_rows: int
    num_row_groups: int
    #: (行组序号, 列路径, min, max)；无统计的列**跳过**（与 BB-1 原实现同口径）。
    #: min/max 保留**原始值**（指纹侧按 `repr` 格式化，与 BB-1 逐字节一致）
    stats: tuple[tuple[int, str, Any, Any], ...]


def table_files(table: str, root: Path, *,
                years: Sequence[int] | None = None) -> list[Path]:
    """表文件清单（升序；`years` 仅对 by_year 布局生效）。

    布局（`tables_meta.Layout`）：BY_YEAR → `root/{table}/YYYY.parquet`；
    SINGLE → `root/{table}/{table}.parquet`；METADATA → `root/metadata/{table}.parquet`。
    """
    meta = TABLES.get(table)
    if meta is None:
        raise ReaderError(f"表未登记: {table}")
    if meta.layout is Layout.BY_YEAR:
        if not _resolve_root(meta, root).is_dir():
            # 列表语义**容忍缺失**（指纹对可选表/合成 root 不炸）；读路径仍由
            # `_list_year_files` 严格守卫（目录缺失即 ReaderError，防静默漏读）
            return []
        files = [p for _y, p in _list_year_files(meta, root)]
        if years is not None:
            wanted = {str(y) for y in years}
            files = [p for p in files if p.stem in wanted]
        return files
    if meta.layout is Layout.SINGLE:
        return [_resolve_root(meta, root) / f"{table}.parquet"]
    return [root / "metadata" / f"{table}.parquet"]


def file_stems(table: str, root: Path, *,
               years: Sequence[int] | None = None) -> list[str]:
    """文件标识清单（by_year 布局 = 年份字符串；single/metadata = 表名）。

    供"逐文件读 schema/数据"的调用方（版本指纹 fast/full 档）**不接触 Path**。
    """
    return [p.stem for p in table_files(table, root, years=years)]


def read_file(table: str, root: Path, *, year: int | None = None,
              columns: Sequence[str] | None = None):
    """单文件读取（低层；**文件缺失 → None**，不抛——调用方语义自理）。

    by_year 布局须给 `year`；single/metadata 布局忽略 `year`。
    """
    meta = TABLES.get(table)
    if meta is None:
        raise ReaderError(f"表未登记: {table}")
    if meta.layout is Layout.BY_YEAR:
        if year is None:
            raise ReaderError(f"表 {table} 为 by_year 布局，须给 year")
        path = _resolve_root(meta, root) / f"{int(year)}.parquet"
    else:
        path = _resolve_root(meta, root) / f"{table}.parquet"
    if not path.is_file():
        return None
    return _read(path, columns=list(columns) if columns else None)


def schema_names(table: str, root: Path, *,
                 year: int | None = None) -> list[str]:
    """列名清单（读 schema，零数据代价）；无文件 → `[]`。"""
    meta = TABLES.get(table)
    if meta is None:
        raise ReaderError(f"表未登记: {table}")
    if meta.layout is Layout.BY_YEAR:
        files = table_files(table, root, years=[year] if year else None)
    else:
        files = table_files(table, root)
    for p in files:
        if p.is_file():
            return list(pq.read_schema(p).names)
    return []


def file_metas(table: str, root: Path, *,
               years: Sequence[int] | None = None) -> list[FileMeta]:
    """逐文件元数据（行数/行组/行组列统计/字节数/mtime）——**不读数据页**。

    用于版本指纹 `meta` 档（BB-1）：数据被重写 → mtime/大小/统计至少之一变化。
    """
    out: list[FileMeta] = []
    for p in table_files(table, root, years=years):
        if not p.is_file():
            continue
        md = pq.ParquetFile(p).metadata
        st = p.stat()
        stats: list[tuple[int, str, Any, Any]] = []
        for gi in range(md.num_row_groups or 0):
            rg = md.row_group(gi)
            for ci in range(rg.num_columns):
                col = rg.column(ci)
                s = col.statistics
                if s is None or not s.has_min_max:
                    continue
                stats.append((gi, col.path_in_schema, s.min, s.max))
        out.append(FileMeta(
            stem=p.stem, size=st.st_size, mtime_ns=st.st_mtime_ns,
            num_rows=md.num_rows or 0, num_row_groups=md.num_row_groups or 0,
            stats=tuple(stats)))
    return out


__all__ = [
    "FileMeta",
    "ReaderError",
    "file_metas",
    "file_stems",
    "iter_day_slices",
    "peek_columns",
    "read_codes",
    "read_file",
    "read_full",
    "read_range",
    "schema_names",
    "table_files",
]
