# -*- coding: utf-8 -*-
"""DataVersion 指纹（PoC-1 任务 1.7；05 §20.3 算法）。

算法（内容寻址，DVC 思想轻量化）：
    sha256( 行数 ‖ 日期范围 ‖ 逐表列级样本哈希(每列首末+等距抽样) )——
    固定抽样规则保证确定性（同数据→同指纹；数据变化→指纹变化）。

两档模式（+ BB-1 新增的元数据档）：
    meta  —— **仅读 Parquet 元数据**（行数 / 行组统计 min-max / 文件大小 / mtime）：
             毫秒级、零数据页读取（BB-1 缺省档；"结构 + 文件级"指纹）。
    fast  —— 仅读取 (date_col, ts_code) 两列：行数+日期范围+键列抽样哈希；
             全库 17 表实测秒级（PoC-1 验证点：[待验证] 落定）；大表（千万行级）
             实测 6 表 × 10 年 ≈ 7.2s（BB-1 实测），故不作缺省。
    full  —— 全列抽样哈希（**数据修正检测**用；耗时随列数增长）。

用法：
    fp = DataVersionFingerprint.compute(["daily", "stk_limit"], root)
    fp.digest  # 合成指纹；fp.tables["daily"].row_count 等明细
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow.compute as pc

from btf.data.tables_meta import TABLES

__all__ = ["DataVersionFingerprint", "TableFingerprint"]


@dataclass(frozen=True)
class TableFingerprint:
    """单表指纹。"""

    table: str
    row_count: int
    date_min: str | None
    date_max: str | None
    sample_hash: str          # 键列抽样哈希（fast）/全列抽样哈希（full）

    def as_dict(self) -> dict[str, object]:
        return {
            "table": self.table, "row_count": self.row_count,
            "date_min": self.date_min, "date_max": self.date_max,
            "sample_hash": self.sample_hash,
        }


@dataclass(frozen=True)
class DataVersionFingerprint:
    """多表合成指纹（manifest.data_version.content_hash 的来源）。"""

    tables: dict[str, TableFingerprint] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        h = hashlib.sha256()
        for name in sorted(self.tables):
            tf = self.tables[name]
            h.update(name.encode())
            h.update(str(tf.row_count).encode())
            h.update((tf.date_min or "").encode())
            h.update((tf.date_max or "").encode())
            h.update(tf.sample_hash.encode())
        return f"sha256:{h.hexdigest()[:16]}"

    def as_dict(self) -> dict[str, object]:
        return {n: t.as_dict() for n, t in sorted(self.tables.items())}


_SAMPLE_STRIDE = 9973  # 固定等距抽样步长（素数，确定性）


def _meta_fingerprint(table: str, metas: Sequence,
                      date_col: str | None) -> TableFingerprint:
    """**元数据档**（BB-1）：只读 Parquet footer——毫秒级、不读数据页。

    组成（全部确定性、内容相关）：行数 ‖ 行组数 ‖ 每行组每列统计 min/max ‖
    文件字节数与 **mtime**。用途：默认开工成本 ~0 的数据指纹（数据被重写 →
    mtime/大小/统计至少之一变化 → 指纹变）；要更严的内容级检测用 fast/full 档。

    `metas`：`core.YearTableStore.file_metas(...)`（EX-4 完全体：本模块不再
    认识磁盘——布局解析/文件列举/stat 全在 `core`）。
    """
    row_count = 0
    date_min: str | None = None
    date_max: str | None = None
    parts: list[str] = []
    for fm in metas:
        row_count += fm.num_rows
        parts.append(f"{fm.stem}:{fm.num_rows}:{fm.num_row_groups}"
                     f":{fm.size}:{fm.mtime_ns}")
        for gi, col_path, lo, hi in fm.stats:
            parts.append(f"{gi}:{col_path}:{lo!r}:{hi!r}")
            if col_path == date_col:
                lo_s, hi_s = str(lo), str(hi)
                date_min = lo_s if date_min is None else min(date_min, lo_s)
                date_max = hi_s if date_max is None else max(date_max, hi_s)
    return TableFingerprint(
        table=table, row_count=row_count, date_min=date_min,
        date_max=date_max,
        sample_hash=_sample_hash(parts, stride=max(1, len(parts) // 64))[:32])


def _sample_hash(values: list, stride: int = _SAMPLE_STRIDE) -> str:
    """等距抽样哈希：首末 + 每 stride 行一行（确定性规则）。"""
    h = hashlib.sha256()
    n = len(values)
    if n == 0:
        return h.hexdigest()
    idx = sorted({0, n - 1, *range(0, n, stride)})
    for i in idx:
        h.update(repr(values[i]).encode("utf-8", errors="replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:32]


def _year_of(stem: str) -> int | None:
    """文件标识 → 年份（by_year 布局）；single/metadata 布局 → None。"""
    return int(stem) if stem.isdigit() else None


def compute(
    tables: Sequence[str],
    root: Path,
    *,
    mode: str = "fast",
    years: Sequence[int] | None = None,
) -> DataVersionFingerprint:
    """计算多表指纹。

    fast：键列（date_col+ts_code）抽样；full：全列抽样（读全列数据，慎用于大表）。

    `years`（BB-1，19 号 §39.4「性能须实测」）：仅取**区间涉及的年文件**——
    回调方按回测区间传入，避免为 3 个月的回测指纹 36 年历史（启动开销与
    语义双收益：指纹只覆盖"实际用到的数据"）。`None` = 全量（原行为）。
    """
    if mode not in ("meta", "fast", "full"):
        raise ValueError(f"mode 须为 meta|fast|full，得 {mode!r}")
    from btf.data.core import YearTableStore

    store = YearTableStore(root)      # EX-4 完全体：**唯一 IO 出口**
    out: dict[str, TableFingerprint] = {}
    for table in tables:
        meta = TABLES.get(table)
        if meta is None:
            raise KeyError(f"表未登记: {table}")
        date_col = meta.date_col
        if mode == "meta":
            out[table] = _meta_fingerprint(
                table, store.file_metas(table, years=years), date_col)
            continue

        row_count = 0
        date_min: str | None = None
        date_max: str | None = None
        sample_parts: list[str] = []

        for stem in store.file_stems(table, years=years):
            year = _year_of(stem)
            schema_names = store.schema_names(table, year=year)
            if not schema_names:
                continue
            cols = ([date_col, "ts_code"] if mode == "fast"
                    else [c for c in schema_names])
            cols = [c for c in cols if c in schema_names]
            t = store.read_file(table, year=year, columns=cols or None)
            if t is None:
                continue
            row_count += t.num_rows
            if date_col is not None and date_col in t.column_names and t.num_rows:
                dmin = pc.min_max(t.column(date_col))["min"].as_py()
                dmax = pc.min_max(t.column(date_col))["max"].as_py()
                date_min = min(x for x in [date_min, dmin] if x is not None)
                date_max = max(x for x in [date_max, dmax] if x is not None)
            # 键列抽样：跨文件累积行号等距（按文件顺序，确定性）
            for c in cols:
                sample_parts.append(
                    f"{stem}:{c}:{_sample_hash(t.column(c).to_pylist())}"
                )

        sample_hash = _sample_hash(sample_parts, stride=max(1, len(sample_parts) // 64))
        out[table] = TableFingerprint(
            table=table, row_count=row_count,
            date_min=date_min, date_max=date_max,
            sample_hash=sample_hash[:32],
        )
    return DataVersionFingerprint(tables=out)
