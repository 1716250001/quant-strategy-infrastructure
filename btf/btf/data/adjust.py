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
        #: **第 4 批（19 号 §199）**：按区间预装载的当前窗口（`{symbol: (lo, hi)}`，
        #: **只扩不缩**）；`qfq` 首次调用时 `_prime` 写入，`adj_factor` 依此取
        #: **区间切片**而非全历史。越界回退告警按实例去重（`_warned_range`）。
        self._primed: dict[str, tuple[str, str]] = {}
        self._warned_range: set[str] = set()

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

    def _load(self, symbol: str, lo_ymd: str = "19900101",
              hi_ymd: str = "20991231") -> tuple[list[str], list[float]]:
        """装载某标的因子行（**第 4 批**：`lo/hi` 区间，缺省＝全历史兜底）。

        键**必须含区间**（`{root}|{symbol}|{lo}|{hi}`）——只按 symbol 缓存会让
        两个不同区间的配置**串用彼此的因子切片**（复权错且不报错，§199 要点 2）。
        """
        # ① 实例级覆盖点（测试/自定义注入——无 IO 纯数学路径，**不走缺失告警**）
        rows = self._cache.get(symbol)
        if rows is not None:
            return rows
        # ② **R62′**：进程级共享缓存（见 `_SHARED_ROWS` 注）；缺失登记与 WARN
        # 仍**逐实例**（披露不得因缓存命中而丢——RC-04 口径不变）。
        key = f"{self.root}|{symbol}|{lo_ymd}|{hi_ymd}"
        rows = self._SHARED_ROWS.get(key)
        if rows is None:
            table = factor_table_of(symbol)          # RC-04：股票/ETF 双表路由
            # KK-1：区间保留全历史（因子表实际覆盖 1991→）；无文件年由
            # YearTableStore.read_year_where 的 path.is_file() 早退，不读盘。
            t = self._store().symbol_range(
                table, symbol, lo_ymd, hi_ymd,
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

    def _prime(self, symbol: str, lo_ymd: str, hi_ymd: str) -> None:
        """按区间预装载（`qfq` 触发；**区间只扩不缩**，§199 要点 1）。

        `lo/hi` 由 `qfq` 按**整年边界**算（前推/后延一年）。只扩不缩的原因：
        `_AdjustedDataSource` 会逐日对**单 bar** 调 `qfq`（`[bar]`），若每次按当日
        收窄，会把历史序列的装载窗口挤成 2 年并反复重装；新区间落在既有窗口内
        ⇒ 跳过，超出则取**并集**重装（一次）。
        """
        if symbol in self._cache:             # 实例级覆盖（测试注入）优先
            return
        cur = self._primed.get(symbol)
        if cur is not None:
            if cur[0] <= lo_ymd and hi_ymd <= cur[1]:
                return
            lo_ymd = min(cur[0], lo_ymd)
            hi_ymd = max(cur[1], hi_ymd)
        self._load(symbol, lo_ymd, hi_ymd)
        self._primed[symbol] = (lo_ymd, hi_ymd)

    # ── 协议实现 ──
    def adj_factor(self, symbol: str, date: TradingDate) -> float:
        """某日复权因子。缺失回退（因子=相对锚，比值自洽优先）：
        - 当日无因子行（两次除权间）→ 最近先前因子（前向填充）；
        - 早于表内首行（因子覆盖晚于价格表，如 600276 1997 上市
           而 adj_factor 自 1999 起）→ 表内最早因子——比值口径自洽；
        - 表内全无该标的 → 1.0 + 计数（研究层不中断；11 §21.1）。

        **第 4 批要点 3**：查询日**落在装载窗口之外**才是「区间收窄截断」，须回退
        全量并告警一次（不得静默返回边界值）；若查询日在窗口内但早于首行（如标的
        上市晚于 `ref`），那是**确实无更早因子**的既有降级路径，不回退、不告警。
        """
        rng = self._primed.get(symbol)
        rows = self._load(symbol, rng[0], rng[1]) if rng else self._load(symbol)
        if (rng and rows[0]
                and not (rng[0] <= date.to_ymd() <= rng[1])):
            old_start = rows[0][0]
            self._primed.pop(symbol, None)
            rows = self._load(symbol)          # 回退全量（缺省参数）
            if (rows[0] and rows[0][0] <= date.to_ymd()
                    and symbol not in self._warned_range):
                self._warned_range.add(symbol)
                logger.warning(
                    "复权因子越界：%s @ %s 早于装载起点 %s → 已回退全量装载"
                    "（区间收窄的兜底路径，19 号 §199）",
                    symbol, date.to_ymd(), old_start)
        ymds, factors = rows
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

        **第 3 批（19 号 §199）**：同一 bar 的 5 个字段本调 5 次 `_af`（实测
        `adj_factor` 643,677 次 / 40.10 s）——改为每 bar **只调一次**并缓存
        `f`；**运算顺序逐字保留 `(a × f) / base`**（改成 `a * (f/base)` 会因
        浮点不结合翻转 9 笔边界成交：`n_fills` 8,574→8,565，不报错）。
        **第 4 批**：按 bars 区间预装载（`lo` 前推一年，覆盖 `ref` 的 bisect 落点）。
        """
        from dataclasses import replace

        if not bars:
            return []
        sym = bars[0].symbol
        # 装载窗口按**整年边界**取（lo 前推一年、hi 后延一年）：单 bar 逐日调
        # `qfq` 时窗口只会随年份推进，避免每日重装（§199 要点 1＋实测）。
        lo = f"{int(bars[0].date.to_ymd()[:4]) - 1}0101"
        hi = f"{int(bars[-1].date.to_ymd()[:4]) + 1}1231"
        self._prime(sym, lo, hi)
        base = self._af_ymd(sym, ref)
        out: list[Bar] = []
        for b in bars:
            f = self._af(b)               # 只调一次；**不预先除 base**（§199 红线）
            out.append(replace(
                b,
                open=b.open * f / base,        # 保持 (a × f) / base
                high=b.high * f / base,
                low=b.low * f / base,
                close=b.close * f / base,
                pre_close=b.pre_close * f / base,
            ))
        return out

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
