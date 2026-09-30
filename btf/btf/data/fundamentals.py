# -*- coding: utf-8 -*-
"""基本面读取与 v7.7 L2 硬筛子（M3 任务 6.2；04 §8.2 H1 数据源）。

数据源（主库只读）：
    daily_basic   每日估值（pe_ttm / pb / dv_ttm / close / total_mv）
                  —— 按 trade_date 精确取数，**原生 PIT**（无前视）
    fina_indicator 财务指标（roe_waa + ann_date + end_date）
                  —— **PIT 过滤：ann_date ≤ 回测日**，且只取年报（end_date 尾 1231）

口径对齐（与 md_core/screening.py::screen_stocks_v75 的差异**显式留痕**）：
    - 估值口径一致：pe_ttm>0 且 <pe_max；pb<pb_max；roe_waa>roe_min；
      dv_ttm>dv_min，**roe/dv 为百分数**（与镜像 JSON units 一致）；
    - **ROE PIT 不同**：md_core 用"缓存里最新年报"（建库时点视角，历史日
      含前视），本模块按 **ann_date ≤ date** 过滤（真正 PIT）——同日对账
      时两者一致，**历史回测日可能分化**，报告须披露；
    - exclude_bj：代码后缀 `.BJ` 排除（与 md_core 一致）；
    - 无 ST/停牌/上市天数过滤（与 md_core 一致）。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from btf.config.paths import MARKET_DATA_DIR

if TYPE_CHECKING:                     # 仅类型注解（运行期惰性 import，无环）
    from btf.data.core import YearTableStore


def _store(root: Path | None) -> YearTableStore:
    """本模块取数口（EX-4 完全体）：**只经 core**，不再直呼 `parquet_reader`。"""
    from btf.data import core as _core

    return _core.YearTableStore(Path(root or MARKET_DATA_DIR))


#: 年报识别（end_date 尾 1231）
def _is_annual(end_date: str) -> bool:
    return str(end_date).endswith("1231")


def daily_basic_of(date_ymd: str, root: Path | None = None,
                   columns: tuple[str, ...] = ("ts_code", "trade_date", "close",
                                               "pe_ttm", "pb", "dv_ttm",
                                               "total_mv")) -> dict[str, dict]:
    """某交易日全市场估值 → {ts_code: {field: value}}（NaN/None 保留为 None）。"""
    table = _store(root).read_range(
        "daily_basic", date_ymd, date_ymd, columns=list(columns))
    out: dict[str, dict] = {}
    for row in table.to_pylist():
        if str(row.get("trade_date")) != date_ymd:
            continue
        out[row["ts_code"]] = {k: row.get(k) for k in columns
                               if k not in ("ts_code", "trade_date")}
    return out


def roe_pit_of(date_ymd: str, root: Path | None = None, *,
               lookback_years: int = 3) -> dict[str, tuple[float, str]]:
    """PIT 年报 ROE：{ts_code: (roe_waa, end_date)}（ann_date ≤ date 的最新年报）。

    fina_indicator 按 end_date 分年存储；PIT 需按 **ann_date** 过滤，故读取
    [date-lookback_years, date] 的年文件后在内存过滤（年报公告最迟次年 4-5 月）。
    """
    import datetime as dt

    root = Path(root or MARKET_DATA_DIR)
    day = dt.date(int(date_ymd[:4]), int(date_ymd[4:6]), int(date_ymd[6:8]))
    start = f"{day.year - lookback_years}0101"
    table = _store(root).read_range(
        "fina_indicator", start, date_ymd,
        columns=["ts_code", "ann_date", "end_date", "roe_waa"])
    best: dict[str, tuple[float, str]] = {}
    for row in table.to_pylist():
        code, roe = row.get("ts_code"), row.get("roe_waa")
        ann, end = str(row.get("ann_date") or ""), str(row.get("end_date") or "")
        if not code or roe is None or not ann or not end:
            continue
        if ann > date_ymd or not _is_annual(end):       # 未公告 / 非年报 → 跳过
            continue
        if code not in best or end > best[code][1]:
            best[code] = (float(roe), end)
    return best


def screen_l2(date_ymd: str, params: dict, *, root: Path | None = None,
              exclude_bj: bool = True,
              universe: set[str] | None = None) -> list[str]:
    """v7.7 L2 硬筛子 → 合格标的代码列表（升序，确定性）。

    params：镜像 JSON 的 `L2_filter`（pe_max/pb_max/roe_min/dv_min，
    **roe_min/dv_min 为百分数**）。
    """
    root = Path(root or MARKET_DATA_DIR)
    pe_max = float(params["pe_max"])
    pb_max = float(params["pb_max"])
    roe_min = float(params["roe_min"])
    dv_min = float(params["dv_min"])

    basics = daily_basic_of(date_ymd, root)
    if not basics:
        return []
    roes = roe_pit_of(date_ymd, root)
    picks: list[str] = []
    for code, row in basics.items():
        if exclude_bj and code.endswith(".BJ"):
            continue
        if universe is not None and code not in universe:
            continue
        pe, pb, dv = row.get("pe_ttm"), row.get("pb"), row.get("dv_ttm")
        roe_entry = roes.get(code)
        if pe is None or pb is None or dv is None or roe_entry is None:
            continue
        roe = roe_entry[0]
        if pe > 0 and pe < pe_max and pb < pb_max and roe > roe_min and dv > dv_min:
            picks.append(code)
    return sorted(picks)


__all__ = ["daily_basic_of", "roe_pit_of", "screen_l2"]
