# -*- coding: utf-8 -*-
"""指数收盘序列取数（19 号架构审查 R2.5 / P1-10；数据层职责）。

与 `analytics.benchmark.relative_metrics`（纯计算，零数据依赖）分离——
`analytics` 与 `data` 同层互禁（pyproject layers 契约），故取数留数据层、
计算留分析层，由 `runtime`（装配层）编排。

对齐口径（关键正确性要求）：
    - 按**策略实际交易日**对齐；缺日 → `IndexSeriesError`（fail-closed，
      禁止前向填充——报告 §C.3.1 明示）；
    - 数据经 `YearTableStore` 共享缓存：两函数**均接受 `store=` 参数**，
      由调用方（`runtime._table_store()`）注入同一实例——**装配期守卫与 run
      期取数共用**，同一批年份 `index_daily` 只读一遍（Z-3，19 号 §26.3）。
      ⚠ 未传 `store=` 时本模块会**临时新建**实例（单测/独立调用的便利路径）；
      生产链路**必须**传——原注释"不造私有缓存"因此在传参缺失时与事实不符
      （累计第 12 处「声明 vs 事实」）。
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from btf.config.paths import MARKET_DATA_DIR

__all__ = ["IndexSeriesError", "load_index_closes", "probe_index_coverage"]


class IndexSeriesError(RuntimeError):
    """指数序列加载/对齐失败（fail-closed：不静默填充）。"""


def load_index_closes(
    symbol: str, start_ymd: str, end_ymd: str, *,
    trading_days: Sequence[str], root: Path | None = None,
    store: object | None = None,
) -> tuple[list[str], list[float]]:
    """基准指数收盘序列（按 `trading_days` 对齐；缺日抛错）。"""
    from btf.data.core import YearTableStore

    days = list(trading_days)
    if not days:
        raise IndexSeriesError("trading_days 为空（无快照？）")
    store = store or YearTableStore(root or MARKET_DATA_DIR, max_entries=8)
    closes: dict[str, float] = {}
    for year in range(int(start_ymd[:4]), int(end_ymd[:4]) + 1):
        for (_code, day), value in store.values_by_symbol_day(
                "index_daily", year, [symbol], "close").items():
            closes[day] = float(value)
    missing = [d for d in days if d not in closes or not closes[d] > 0]
    if missing:
        raise IndexSeriesError(
            f"基准 {symbol} 在 {len(missing)} 个策略交易日无收盘价"
            f"（缺日禁止前向填充）：{missing[:5]}…")
    return days, [closes[d] for d in days]


def probe_index_coverage(
    symbol: str, start_ymd: str, end_ymd: str, *,
    root: Path | None = None, store: object | None = None,
) -> int:
    """区间内基准可用收盘日数（**装配期守卫**；P1-NEW-6，19 号 §23.1/§23.3）。

    为什么需要：基准校验原发生在 `run()` **之后**（`_benchmark_metrics`）——
    基准号拼错或区间不覆盖，须**跑完整段回测**才报错（长区间 = 白烧十几分钟）；
    而"区间"本身早有装配期守卫（`check_coverage`），两者标准不一致。

    口径：只数区间内该指数的收盘行数（`index_daily` 谓词下推，仅取命中行）。
    返回 0 → 调用方应 **ConfigError**（装配期即拒，不再白跑）；完整逐日对齐
    校验仍在 run 结束（策略交易日对齐是运行时信息）。
    """
    from btf.data.core import YearTableStore

    store = store or YearTableStore(root or MARKET_DATA_DIR, max_entries=4)
    n = 0
    for year in range(int(start_ymd[:4]), int(end_ymd[:4]) + 1):
        for (_code, day), value in store.values_by_symbol_day(
                "index_daily", year, [symbol], "close").items():
            if start_ymd <= day <= end_ymd and value is not None:
                n += 1
    return n
