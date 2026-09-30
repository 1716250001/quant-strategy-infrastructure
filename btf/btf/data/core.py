# -*- coding: utf-8 -*-
"""表级资源管理（19 号架构审查 §9.1 `TabularStore` 的最小落地；Q2 方案 A）。

为什么存在（报告 §8 判定）：`data.core` 缺席 → 每个数据消费模块各自造轮子
（`liq.index_pct` 为取 1 个标量读整年表；`state._limit_*` 自建按日索引；
`adjust` 为 N 个标的重载全表）→ 性能与内存问题反复重演。本模块把
「年表装载 / 按日索引 / 按标的区间 / 有界缓存」提升为共享基础设施。

**做什么**：让业务模块以 O(1) 取到「某日某表」或「某标的某区间」；
同 (table, year, columns) 二次调用零 IO（装载计数可断言）。

**不做什么**（边界，报告 §6.1）：任何业务判定——LIQ/筛选/复权/状态语义留在
`data.*` 专业模块；本模块不认识"涨跌停""停牌""基准"这些概念，
只认识「表 + 日 + 标的 + 列」。

设计取舍（Q2=A 最小抽象）：不做 `filters=` 下推与 `memory_map`（报告 §11
IO-8/IO-9 列为后续可选加项）；LRU 以「已装载年表条目数」为界，默认 4 条
（覆盖十年以上滚动访问的窗口内复用；超限淘汰最旧）。
"""
from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from btf.config.paths import MARKET_DATA_DIR
from btf.data.tables_meta import TABLES

__all__ = ["YearTableStore", "day_index"]


def day_index(dates: list[str]) -> dict[str, tuple[int, int]]:
    """已排序日期列的日区间索引 {ymd: (lo, hi)}（一次线性扫描）。"""
    index: dict[str, tuple[int, int]] = {}
    start = 0
    n = len(dates)
    for i in range(1, n + 1):
        if i == n or dates[i] != dates[start]:
            index[dates[start]] = (start, i)
            start = i
    return index


class YearTableStore:
    """年表装载 + 按日索引 + 有界缓存（**线程安全**；只读主库）。

    容量：`max_entries` 控制缓存的 (table, year, columns) 条目数（默认 4）；
    淘汰为插入序 FIFO——按年推进的访问模式下命中率与 LRU 等价。

    并发（X-9，19 号 §17.4.4）：`precompute_liq_series` 用
    `ThreadPoolExecutor` 逐年并行，而本 store 被**共享**——原注释称"各年
    独立只读、无共享状态"与事实不符（`_cache`/`load_count`/`hit_count` 是
    无锁普通 dict/int；FIFO 淘汰 `next(iter(...))` 在并发下有竞态）。
    现以 `threading.Lock` 保护**缓存与计数器**（IO 在锁外：pyarrow 读盘释放
    GIL → 并行收益保留），并做装载双检：并发重复装载时**只计一次**
    （`load_count` 语义 = 新建缓存条目数，故性能断言可靠）。
    """

    def __init__(self, root: Path | None = None, max_entries: int = 4):
        self.root = Path(root) if root else MARKET_DATA_DIR
        self.max_entries = int(max_entries)
        self._lock = threading.Lock()
        self._cache: dict[
            tuple[str, int, tuple[str, ...]],
            tuple[pa.Table | None, dict[str, tuple[int, int]], str, str],
        ] = {}
        #: 装载计数（性能断言用：同参数二次调用不得增长）
        self.load_count = 0
        self.hit_count = 0

    # ── 装载（唯一 IO 入口）──
    def load(self, table: str, year: int, columns: Sequence[str], *,
             sorted_rows: bool = True) -> pa.Table | None:
        """**公开**年表入口（`_load` 薄封装，返回表本体）。

        EX-4（19 号 §9.2）：`data.core` 是全系统唯一允许"认识磁盘"的公共模块
        ——业务模块（`data.quality` 等）经本方法取数，不直呼 `parquet_reader`。
        """
        return self._load(table, year, columns, sorted_rows=sorted_rows)[0]

    def _load(
        self, table: str, year: int, columns: Sequence[str], *,
        sorted_rows: bool = True,
    ) -> tuple[pa.Table | None, dict[str, tuple[int, int]], str, str]:
        """年表装载（缓存）。返回 (表, 日索引, 日期列, 键列)。

        `sorted_rows=True`（默认）：按 (日期, 键) 排序并建日索引——`day_map`/
        `day_codes` 的前提；`False`：原序装载（**join/group_by 等向量化路径
        不需要有序**，省 1s/年/表——19 号报告 §11 的实测结论）。
        """
        key = (table, year, tuple(columns), sorted_rows)
        with self._lock:                       # X-9：缓存/计数器临界区
            cached = self._cache.get(key)
            if cached is not None:
                self.hit_count += 1
                return cached
        meta = TABLES.get(table)
        if meta is None:
            raise ValueError(f"表未登记: {table}")
        date_col = meta.date_col
        if date_col is None:
            raise ValueError(f"表 {table} 无日期列（静态表）")
        key_col = "ts_code"
        path = self.root / table / f"{year}.parquet"
        if not path.is_file():
            entry: tuple[pa.Table | None, dict[str, tuple[int, int]], str, str] = (
                None, {}, date_col, key_col)
        else:
            t = pq.read_table(path, columns=list(columns))
            if (sorted_rows and t.num_rows and key_col in t.column_names):
                t = t.sort_by([(date_col, "ascending"), (key_col, "ascending")])
            dates = (t.column(date_col).to_pylist()
                     if (sorted_rows and t.num_rows) else [])
            entry = (t, day_index(dates), date_col, key_col)
        with self._lock:
            cached = self._cache.get(key)      # 双检：并发期间他线程可能已装载
            if cached is not None:
                return cached
            if len(self._cache) >= self.max_entries:
                self._cache.pop(next(iter(self._cache)))      # FIFO 淘汰
            self._cache[key] = entry
            self.load_count += 1     # 语义：新建缓存条目数（并发重复不重复计）
        return entry

    def read_year_where(
        self, table: str, year: int, columns: Sequence[str],
        key_values: Sequence[str],
    ) -> pa.Table | None:
        """**谓词下推**装载（parquet 原生 `filters=`，报告 §11 IO-8 的最小落地）。

        用于「小标的集整年查询」——主库年文件按 `ts_code` 聚集，row-group 级
        过滤可跳过绝大多数数据块（`index_daily` 2.53M 行/年 → 两只指数行）。
        """
        key = (table, year, tuple(columns), ("where", tuple(sorted(key_values))))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self.hit_count += 1
                return cached[0]
        path = self.root / table / f"{year}.parquet"
        out: pa.Table | None = None
        if path.is_file():
            out = pq.read_table(
                path, columns=list(columns),
                filters=[("ts_code", "in", list(key_values))])
        with self._lock:
            cached = self._cache.get(key)      # 双检（同 `_load`）
            if cached is not None:
                return cached[0]
            if len(self._cache) >= self.max_entries:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (out, {}, "", "ts_code")
            self.load_count += 1
        return out

    # ── 查询（O(1)/日；切片 + 单日物化）──
    def day_map(
        self, table: str, ymd: str, *, key: str = "ts_code", value: str,
        columns: tuple[str, ...] | None = None,
    ) -> dict[str, float | None]:
        """某日 → {键: 数值列}（当日行物化，~数千项；无该日 → 空 dict）。"""
        cols = columns or (key, "trade_date", value)
        t, index, _date_col, _key_col = self._load(table, int(ymd[:4]), cols)
        span = index.get(ymd)
        if t is None or span is None:
            return {}
        lo, hi = span
        day = t.slice(lo, hi - lo)
        out: dict[str, float | None] = {}
        raw = day.column(value).to_pylist()
        for code, item in zip(day.column(key).to_pylist(), raw, strict=True):
            out[code] = None if item is None else float(item)
        return out

    def day_codes(self, table: str, ymd: str, *, key: str = "ts_code") -> list[str]:
        """某日键列表（升序——装载时已按 (date, key) 排序）。"""
        t, index, _date_col, _key_col = self._load(
            table, int(ymd[:4]), (key, "trade_date"))
        span = index.get(ymd)
        if t is None or span is None:
            return []
        lo, hi = span
        return [str(c) for c in t.slice(lo, hi - lo).column(key).to_pylist()]

    def days(self, table: str, year: int) -> list[str]:
        """某年全部交易日（升序）。"""
        _t, index, _date_col, _key_col = self._load(table, year, ("trade_date",))
        return sorted(index)

    def load_year(
        self, table: str, year: int, columns: Sequence[str], *,
        sorted_rows: bool = True,
    ) -> tuple[pa.Table | None, dict[str, tuple[int, int]]]:
        """公开的年表句柄 (表, 日索引)——供业务模块做 **Arrow 向量化**运算
        （如 `liq` 的逐日跌停统计走 join+group_by，而非逐日 Python 切片；
        业务判定仍留在业务模块，报告 §6.1 边界）。

        `sorted_rows=False`：join/group_by 路径免排序（省 ~1s/年/表）。"""
        t, index, _date_col, _key_col = self._load(
            table, year, tuple(columns), sorted_rows=sorted_rows)
        return t, index

    def values_by_symbol_day(
        self, table: str, year: int, symbols: Sequence[str], value: str,
        *, date_col: str = "trade_date",
    ) -> dict[tuple[str, str], float]:
        """{(标的, 日): 数值}——**小标的集**的整年查询（Arrow `is_in` 过滤后
        仅物化命中行）。语义：缺值跳过（None 不入表）。

        反面案例（本方法要解决的，报告 §C.1 证据 A）：`liq.index_pct` 为取
        1 个标量读整年 2.53M 行并逐行遍历。
        """
        want = tuple(dict.fromkeys(("ts_code", date_col, value)))
        t = self.read_year_where(table, year, want, list(symbols))
        if t is None or not t.num_rows:
            return {}
        out: dict[tuple[str, str], float] = {}
        for code, day, item in zip(t.column("ts_code").to_pylist(),
                                   t.column(date_col).to_pylist(),
                                   t.column(value).to_pylist(), strict=True):
            if item is not None:
                out[(str(code), str(day))] = float(item)
        return out

    # ── 研究层：某标的区间（P1-9 反面案例：N 标的不再 N 次全表）──
    def symbol_range(
        self, table: str, symbol: str, start_ymd: str, end_ymd: str,
        columns: Sequence[str],
    ) -> pa.Table:
        """某标的日期区间（逐年 is_in 过滤 + 区间过滤 + 拼接）。"""
        cols = tuple(dict.fromkeys((*columns, "ts_code", "trade_date")))
        y0, y1 = int(start_ymd[:4]), int(end_ymd[:4])
        parts: list[pa.Table] = []
        for year in range(y0, y1 + 1):
            t, _index, date_col, _key_col = self._load(table, year, cols)
            if t is None or not t.num_rows:
                continue
            mask = pc.equal(t.column("ts_code"), symbol)
            if year == y0 or year == y1:
                mask = pc.and_(
                    mask,
                    pc.and_(pc.greater_equal(t.column(date_col), start_ymd),
                            pc.less_equal(t.column(date_col), end_ymd)))
            part = t.filter(mask)
            if part.num_rows:
                parts.append(part)
        if not parts:
            return pa.table({})
        return pa.concat_tables(parts)

    # ── 淘汰与统计 ──
    def evict(self, *, year_below: int | None = None,
              table: str | None = None) -> int:
        """显式淘汰（报告 §9.1：替代散落各处的 `_evict_year_cache`）。"""
        with self._lock:
            stale = [k for k in self._cache
                     if (year_below is not None and k[1] < year_below)
                     or (table is not None and k[0] == table)]
            for k in stale:
                del self._cache[k]
        return len(stale)

    def stats(self) -> dict[str, Any]:
        """装载/命中统计（性能断言与 `--verbose` 观测用）。"""
        with self._lock:
            return {"entries": len(self._cache), "loads": self.load_count,
                    "hits": self.hit_count}

    # ══════════════════════════════════════════════════════════
    # EX-4 完全体（19 号 §9.2 / §47.4 余项 ② / §49）：**全系统唯一 IO 出口**
    # ══════════════════════════════════════════════════════════
    # 以下 7 个方法逐一委托 `parquet_reader`（同一实现、**行为逐位一致**，
    # 不引入新缓存——语义与直呼 reader 完全相同）。纪律：
    #   · `btf.data.parquet_reader` **只允许本模块 import**（契约新 9，零豁免）；
    #   · `pyarrow.parquet` 只允许本模块与 `parquet_reader` import；
    #   · 守卫 = `tools/check_io_boundary.py`（`bt check` 第 9 项）。
    # 为什么值得：IO 的"算术"（谓词下推/布局解析/memory_map/单位换算）此前分散在
    # 7 个模块的 20+ 处调用点，改一处口径要全库搜；收拢后**只有一条路径**。

    def read_full(self, table: str, *, columns: Sequence[str] | None = None,
                  years: Sequence[int] | None = None):
        """全表读取（single/metadata 布局主用；by_year 慎用——全量内存）。"""
        from btf.data import parquet_reader as pr

        return pr.read_full(table, self.root, columns=columns, years=years)

    def read_range(self, table: str, start_ymd: str, end_ymd: str, *,
                   columns: Sequence[str] | None = None):
        """日期区间读取（布局内先按年裁剪，再按日期列过滤）。"""
        from btf.data import parquet_reader as pr

        return pr.read_range(table, self.root, start_ymd, end_ymd,
                             columns=columns)

    def peek_columns(self, table: str) -> list[str]:
        """列名探测（零数据代价）；缺失列由调用方语义回退。"""
        from btf.data import parquet_reader as pr

        return pr.peek_columns(table, self.root)

    def read_codes(self, table: str, symbols: Sequence[str], start_ymd: str,
                   end_ymd: str, *, columns: Sequence[str] | None = None):
        """多标的区间读取（谓词下推：`ts_code IN (...) AND 日期区间`）。"""
        from btf.data import parquet_reader as pr

        return pr.read_codes(table, self.root, symbols, start_ymd, end_ymd,
                             columns=columns)

    def iter_day_slices(self, table: str, start_ymd: str, end_ymd: str, *,
                        columns: Sequence[str] | None = None,
                        symbols: Sequence[str] | None = None):
        """引擎热路径：按年流式 + 列式行区间（零 Python 对象化）。"""
        from btf.data import parquet_reader as pr

        return pr.iter_day_slices(table, self.root, start_ymd, end_ymd,
                                  columns=columns, symbols=symbols)

    def read_file(self, table: str, *, year: int | None = None,
                  columns: Sequence[str] | None = None):
        """单文件读取（低层；文件缺失 → None）。状态/质检/指数成分等窄读用。"""
        from btf.data import parquet_reader as pr

        return pr.read_file(table, self.root, year=year, columns=columns)

    def schema_names(self, table: str, *, year: int | None = None) -> list[str]:
        """列名清单（读 schema，零数据代价）；无文件 → `[]`。"""
        from btf.data import parquet_reader as pr

        return pr.schema_names(table, self.root, year=year)

    def file_metas(self, table: str, *, years: Sequence[int] | None = None):
        """逐文件元数据（行数/行组统计/大小/mtime）——版本指纹元数据档用。"""
        from btf.data import parquet_reader as pr

        return pr.file_metas(table, self.root, years=years)

    def file_stems(self, table: str, *,
                   years: Sequence[int] | None = None) -> list[str]:
        """文件标识清单（by_year = 年份字符串；single/metadata = 表名）。"""
        from btf.data import parquet_reader as pr

        return pr.file_stems(table, self.root, years=years)
