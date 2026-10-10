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

复权因子表路由（**RC-04，2026-10-05**）：
    - **股票 → `adj_factor`**（1991-07-03 起）；**ETF/LOF → `fund_adj`**
      （1999-10-22 起）——路由复用 `tables_meta.factor_table_of`
      （与行情路由同一套代码段判定，禁止第二套 = 双真源）。
    - 原实现对**任意**标的都读 `adj_factor`（只含股票）⇒ ETF 必然无行 ⇒
      静默回落 1.0，**份额折算/不定期折算的价格跳变不被抹平**。

因子缺失降级（11 §21.1 PoC-3 验证点；RC-04 起**不得静默**）：
    - 标的当日无因子行（新上市前/停牌残行/数据缺失）→ 回退**最近先前
      因子**（前向填充，除权静默期语义：因子在两次除权间恒定）；
    - 起点前无任何因子（早于表起点）→ 表内最早因子（相对锚）；
    - 表内全无该标的 → 1.0 + 计数 + **`logger.warning`**（原实现只计数），
      并经 `degraded_notes()` 供报告「假设与披露」消费。

依赖：pyarrow（data 层允许；domain 零依赖纪律不受影响）。
"""
from __future__ import annotations

import logging
from bisect import bisect_right
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, Protocol

from btf.config.paths import MARKET_DATA_DIR
from btf.data.tables_meta import factor_table_of
from btf.domain.cache import BoundedDict
from btf.domain.market import Bar
from btf.domain.types import TradingDate

logger = logging.getLogger(__name__)

__all__ = ["AdjustService", "TushareAdjustService"]


class AdjustService(Protocol):
    """复权服务协议（04 §8.2.4 签名照抄）。研究层专用，不进入引擎记账。"""

    def hfq(self, bars: Sequence[Bar]) -> Sequence[Bar]: ...
    def qfq(self, bars: Sequence[Bar], ref: TradingDate) -> Sequence[Bar]: ...
    def adj_factor(self, symbol: str, date: TradingDate) -> float: ...


class TushareAdjustService:
    """基于主库**复权因子表**的消费侧换算（单标的缓存，同参数可重复调用）。

    因子表按标的**自动路由**（RC-04）：股票 `adj_factor` / ETF·LOF `fund_adj`
    （`tables_meta.factor_table_of`）。

    缓存粒度：per symbol 的 (日期升序因子列表)，首次访问装载该标的
    全历史（read_range 区间=表全量），后续 adj_factor 查询 bisect——
    研究层"单标的全历史序列"是典型访问模式，一次装载全程复用。
    """

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else MARKET_DATA_DIR
        #: **实例级因子行覆盖点**（`{symbol: (ymd 升序, factor 平行)}`）：`_load`
        #: **先查此表**——测试以「覆盖 `_cache`」实现无 IO 纯数学（`tests/unit/
        #: test_adjust.py` 既有契约），自定义调用方亦可注入；未命中才走
        #: **进程级共享缓存**（`_SHARED_ROWS`，R62′）。
        self._cache: dict[str, tuple[list[str], list[float]]] = {}
        self._tstore: object | None = None      # 表级共享装载（P1-9）
        # (symbol, ymd) 命中计数 / 回退计数（降级行为审计，11 §21.1 验证点）
        self.n_missing_fallback = 0
        self.n_fill_forward = 0
        # RC-04：**表内全无**该标的的集合（回落 1.0）——供 `degraded_notes()`
        # 生成报告披露（原实现只计数、无告警、无披露通道）。
        self.missing_factor_symbols: set[str] = set()
        #: 缺表 WARN 的**实例级**去重（同一实例同一标的只告警一次，避免刷屏；
        #: 跨实例不去重——缺失披露按 run 独立，测试/报告口径不受进程历史影响）。
        self._warned: set[str] = set()

    #: **R62′（N64，2026-10-10；19 号 §118.3-3）**：因子行**进程级共享缓存**
    #: （键 = `{root}|{symbol}` ⇒ 多库安全）。动机（自查实测）：引擎消费侧视图
    #: 每个 run 新建服务，若逐实例装载，**每标的首次装载 ≈1.5–1.9s**（全历史
    #: `adj_factor`/`fund_adj` 扫描）⇒ 43 池/网格/滚动验证（每折一 run）线性放大；
    #: 共享后**同进程重复访问 ≈0.04s/标的**（纯换算）。**计数与披露仍 per 实例**
    #: （`missing_factor_symbols`/`n_*` 逐 run 登记——共享缓存不得吞掉降级披露）。
    _SHARED_ROWS: ClassVar[BoundedDict[str, tuple[list[str], list[float]]]] = BoundedDict(
        maxsize=8_000, name="adjust.factor.shared")

    def _store(self):
        """共享表级资源（19 号审查 P1-9：原实现逐标的 read_range 全历史
        （36 年文件、1,690 万行/标的）——N 标的 = N 次全表装载；经
        `YearTableStore.symbol_range` 单次装载按 symbol 过滤后复用）。"""
        if self._tstore is None:
            from btf.data.core import YearTableStore

            self._tstore = YearTableStore(self.root, max_entries=8)
        return self._tstore

    def _load(self, symbol: str) -> tuple[list[str], list[float]]:
        # ① 实例级覆盖点（测试/自定义注入——无 IO 纯数学路径，**不走缺失告警**）
        rows = self._cache.get(symbol)
        if rows is not None:
            return rows
        # ② **R62′**：进程级共享缓存（见 `_SHARED_ROWS` 注）；缺失登记与 WARN
        # 仍**逐实例**（披露不得因缓存命中而丢——RC-04 口径不变）。
        key = f"{self.root}|{symbol}"
        rows = self._SHARED_ROWS.get(key)
        if rows is None:
            table = factor_table_of(symbol)          # RC-04：股票/ETF 双表路由
            t = self._store().symbol_range(
                table, symbol, "19900101", "20991231",
                ["adj_factor"])
            if t.num_rows:
                t = t.sort_by([("trade_date", "ascending")])
                rows = (t.column("trade_date").to_pylist(),
                        t.column("adj_factor").to_pylist())
            else:
                rows = ([], [])
            self._SHARED_ROWS[key] = rows
        if not rows[0]:
            # RC-04：缺失**必须告警**（原实现静默回落 1.0）——含表名与口径说明；
            # 逐实例登记（degraded_notes 用），进程级只 WARN 一次（防刷屏）。
            self.missing_factor_symbols.add(symbol)
            if symbol not in self._warned:
                self._warned.add(symbol)
                logger.warning(
                    "复权因子缺失：%s 在 `%s` 表无任何行 → 因子按 1.0 回落"
                    "（未复权序列的份额折算跳变**不被抹平**）；"
                    "报告披露见 degraded_notes()（RC-04）",
                    symbol, factor_table_of(symbol))
        return rows

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

    # ── 降级披露（RC-04；与 `StateSynthesizer.degraded_notes()` 同机制）──
    def degraded_notes(self) -> tuple[str, ...]:
        """复权降级说明（供报告「假设与披露」消费；viz 不 import 本模块）。

        形制与 `data.state.StateSynthesizer.degraded_notes()` 一致（`tuple[str]`），
        由调用方注入 `Assumptions.degraded`——「缺失即披露」，不静默。
        """
        if not self.missing_factor_symbols:
            return ()
        shown = sorted(self.missing_factor_symbols)[:5]
        suffix = "…" if len(self.missing_factor_symbols) > len(shown) else ""
        tables = sorted({factor_table_of(s) for s in self.missing_factor_symbols})
        return (
            f"复权因子缺失回退：{self.n_missing_fallback} 次 / "
            f"{len(self.missing_factor_symbols)} 只标的"
            f"（{', '.join(shown)}{suffix}；查表 {'/'.join(tables)}）"
            f"——因子按 1.0 回落，份额折算/不定期折算的价格跳变**未被抹平**"
            f"（RC-04）",
        )
