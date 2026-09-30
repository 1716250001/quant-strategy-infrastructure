# -*- coding: utf-8 -*-
"""回测区间覆盖度守卫（19 号架构审查 P1-11 / §C.3.2；R2.5）。

**要解决的问题**（报告证据 C1）：`runtime` 直接取 `period.start/end`，对
「区间早于必需表起点」零校验——实测 2008 年前的股票回测**可以正常跑完并
出报告，但涨跌停约束静默失效**（`stk_limit` 起点 20080102 → `limit_up=None`
→ `touch_limit_up` 返回 False），成交系统性偏乐观且不披露。比"跑不了"更危险。

规则（fail-closed 默认）：
    - `start > end` → fatal（原实现零校验）
    - 区间早于**风控/撮合必需表**（`stk_limit`）起点 → fatal，
      除非配置显式 `data.allow_degraded_limit=true`（写入生效配置 →
      报告假设章节强制披露，见 `viz.report._derived_disclosures`）
    - 区间早于**非必需表**起点（如 `etf_limit`/`adj_factor`）→ degraded
      （登记进报告披露，不阻断）
"""
from __future__ import annotations

from dataclasses import dataclass

from btf.data.tables_meta import TABLES

__all__ = ["CoverageReport", "check_coverage"]

#: 股票涨跌停约束的必需表（缺失 = 成交乐观偏差，默认 fatal）
REQUIRED_TABLES: tuple[str, ...] = ("stk_limit",)
#: 缺失仅登记降级的表（研究层/历史区间常见）
DEGRADED_TABLES: tuple[str, ...] = ("etf_limit", "adj_factor")


@dataclass(frozen=True)
class CoverageReport:
    """区间覆盖度报告（装配期产出；进日志 + 报告披露通道）。"""

    period: tuple[str, str]
    fatal: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.fatal

    def notes(self) -> tuple[str, ...]:
        return self.fatal + self.degraded


def check_coverage(
    start_ymd: str, end_ymd: str, *,
    required: tuple[str, ...] = REQUIRED_TABLES,
    degraded: tuple[str, ...] = DEGRADED_TABLES,
) -> CoverageReport:
    """装配期校验：区间 ∩ 各表起点（表起点来自 `tables_meta` 单一真源）。"""
    fatal: list[str] = []
    degraded_notes: list[str] = []
    if start_ymd > end_ymd:
        fatal.append(f"period 起止倒置：{start_ymd} > {end_ymd}")
    for table in required:
        meta = TABLES.get(table)
        tbl_start = meta.start if meta is not None else None
        if tbl_start and start_ymd < tbl_start:
            fatal.append(
                f"{table} 未覆盖 {start_ymd}~{tbl_start}（表起点 {tbl_start}）"
                f"——涨跌停/停牌约束在该区间不可用，成交偏乐观")
    for table in degraded:
        meta = TABLES.get(table)
        tbl_start = meta.start if meta is not None else None
        if tbl_start and start_ymd < tbl_start:
            degraded_notes.append(
                f"{table} 未覆盖 {start_ymd}~{tbl_start}（起点 {tbl_start}）"
                f"——相关功能降级/回退")
    return CoverageReport(period=(start_ymd, end_ymd),
                          fatal=tuple(fatal), degraded=tuple(degraded_notes))
