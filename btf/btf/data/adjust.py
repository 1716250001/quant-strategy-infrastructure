# -*- coding: utf-8 -*-
"""AdjustService：hfq/qfq 消费侧换算（PoC-3 任务 3.1；04 §8.2.4）。

口径纪律（v0.3 H4，全文唯一记账基的补集）：
    - 引擎记账/估值**一律不复权名义价**，本服务**不进入引擎记账**——
      仅用于研究层指标计算与图表展示（如奇点周线 J/CCI 防价格跳变失真）；
    - 库内无 hfq/qfq 字段，复权一律消费侧计算（05 §9.4 T-02）：
        hfq(t) = price(t) × adj_factor(t)
        qfq(t) = price(t) × adj_factor(t) / adj_factor(ref)
    - ref = 回测起点日 → 区间前复权（回测内一致性）；
      ref = 最新日 → 全历史前复权（仅研究层；引擎不用）。

adj_factor 缺失降级（11 §21.1 PoC-3 验证点）：
    - 标的当日无因子行（新上市前/停牌残行/数据缺失）→ 回退**最近先前
      因子**（前向填充，除权静默期语义：因子在两次除权间恒定）；
    - 起点前无任何因子（早于 adj_factor 表起点 1991-07-03）→ 1.0 并
      计数登记（caller 决定 Strict/Warn），不中断研究层计算。

依赖：pyarrow（data 层允许；domain 零依赖纪律不受影响）。
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from btf.config.paths import MARKET_DATA_DIR
from btf.domain.cache import BoundedDict
from btf.domain.market import Bar
from btf.domain.types import TradingDate

__all__ = ["AdjustService", "TushareAdjustService"]


class AdjustService(Protocol):
    """复权服务协议（04 §8.2.4 签名照抄）。研究层专用，不进入引擎记账。"""

    def hfq(self, bars: Sequence[Bar]) -> Sequence[Bar]: ...
    def qfq(self, bars: Sequence[Bar], ref: TradingDate) -> Sequence[Bar]: ...
    def adj_factor(self, symbol: str, date: TradingDate) -> float: ...


class TushareAdjustService:
    """基于主库 adj_factor 表的消费侧换算（单标的缓存，同参数可重复调用）。

    缓存粒度：per symbol 的 (日期升序因子列表)，首次访问装载该标的
    全历史（read_range 区间=表全量），后续 adj_factor 查询 bisect——
    研究层"单标的全历史序列"是典型访问模式，一次装载全程复用。
    """

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else MARKET_DATA_DIR
        # symbol → (ymd 升序列表, factor 平行列表)；PF-9/P2-5：LRU 有界
        self._cache: BoundedDict[str, tuple[list[str], list[float]]] = BoundedDict(
            maxsize=8_000, name="adjust.factor")
        self._tstore: object | None = None      # 表级共享装载（P1-9）
        # (symbol, ymd) 命中计数 / 回退计数（降级行为审计，11 §21.1 验证点）
        self.n_missing_fallback = 0
        self.n_fill_forward = 0

    # ── 装载 ──
    def _store(self):
        """共享表级资源（19 号审查 P1-9：原实现逐标的 read_range 全历史
        （36 年文件、1,690 万行/标的）——N 标的 = N 次全表装载；经
        `YearTableStore.symbol_range` 单次装载按 symbol 过滤后复用）。"""
        if self._tstore is None:
            from btf.data.core import YearTableStore

            self._tstore = YearTableStore(self.root, max_entries=8)
        return self._tstore

    def _load(self, symbol: str) -> tuple[list[str], list[float]]:
        if symbol not in self._cache:
            t = self._store().symbol_range(
                "adj_factor", symbol, "19900101", "20991231",
                ["adj_factor"])
            if t.num_rows:
                t = t.sort_by([("trade_date", "ascending")])
                self._cache[symbol] = (t.column("trade_date").to_pylist(),
                                       t.column("adj_factor").to_pylist())
            else:
                self._cache[symbol] = ([], [])
        return self._cache[symbol]

    # ── 协议实现 ──
    def adj_factor(self, symbol: str, date: TradingDate) -> float:
        """某日复权因子。缺失回退（因子=相对锚，比值自洽优先）：
        - 当日无因子行（两次除权间）→ 最近先前因子（前向填充）；
        - 早于表内首行（因子覆盖晚于价格表，如 600276 1997 上市
           而 adj_factor 自 1999 起）→ 表内最早因子——比值口径自洽；
        - 表内全无该标的 → 1.0 + 计数（研究层不中断；11 §21.1）。
        """
        ymds, factors = self._load(symbol)
        i = bisect_right(ymds, date.to_ymd()) - 1
        if i >= 0:
            if ymds[i] != date.to_ymd():
                self.n_fill_forward += 1
            return factors[i]
        if ymds:
            self.n_missing_fallback += 1
            return factors[0]             # 最早行（相对锚，非 1.0 假设）
        self.n_missing_fallback += 1
        return 1.0

    def hfq(self, bars: Sequence[Bar]) -> Sequence[Bar]:
        """后复权：price × adj_factor(t)（含分红再投资口径）。"""
        from dataclasses import replace

        return [
            replace(
                b,
                open=b.open * self._af(b),
                high=b.high * self._af(b),
                low=b.low * self._af(b),
                close=b.close * self._af(b),
                pre_close=b.pre_close * self._af(b),
            )
            for b in bars
        ]

    def qfq(self, bars: Sequence[Bar], ref: TradingDate) -> Sequence[Bar]:
        """前复权（区间口径）：price × adj_factor(t) / adj_factor(ref)。

        ref=回测起点 → 区间前复权（回测内一致性，规避基准漂移——
        对照 alt 库 us_daily_qfq 整表重建教训，04 §8.2.4）。
        """
        from dataclasses import replace

        base = self._af_ymd(bars[0].symbol, ref) if bars else 1.0
        return [
            replace(
                b,
                open=b.open * self._af(b) / base,
                high=b.high * self._af(b) / base,
                low=b.low * self._af(b) / base,
                close=b.close * self._af(b) / base,
                pre_close=b.pre_close * self._af(b) / base,
            )
            for b in bars
        ]

    # ── 内部 ──
    def _af(self, bar: Bar) -> float:
        return self.adj_factor(bar.symbol, bar.date)

    def _af_ymd(self, symbol: str, date: TradingDate) -> float:
        return self.adj_factor(symbol, date)
