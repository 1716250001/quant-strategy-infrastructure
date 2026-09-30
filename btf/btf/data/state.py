# -*- coding: utf-8 -*-
"""交易日状态面板合成（PoC-1 任务 1.6；04 §8.2.3 / 评审 H1 立项的核心增量层）。

五表合成 per (symbol, date) 的 TradingState：
    stk_limit   → limit_up_price / limit_down_price（股票）
    etf_limit   → 同上（**ETF，V3-2；起点 2019-06-26**，E3；更早由 BoardRule
                  按 pre_close ±10% 计算回退并登记 `fallback_limit_dates` 披露）
    suspend_d   → is_suspended（口径=当天在表即停牌）
    namechange  → is_st（name 含 'ST' ∧ start≤d<end；end 空视为仍生效）
    stock_basic → is_delisted（delist_date ≤ d）/ list_date（宇宙过滤在 universe 层）
    daily       → is_limit_up / is_limit_down（收盘触及，容差 1e-4；**股票口径**）

设计：年度文件级缓存 + 按需列裁剪；同参数调用可重复（DataFeed 纪律）。
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import date as _date
from pathlib import Path

import pyarrow.compute as pc

from btf.config.paths import MARKET_DATA_DIR
from btf.data.tables_meta import TABLES as _TABLES
from btf.data.tables_meta import daily_table_of
from btf.domain.market import TradingState, touch_limit_down, touch_limit_up
from btf.domain.types import AssetClass, Instrument, TradingDate, parse_trading_date, rule_for

#: 股票涨跌停表起点（P1-11/P2-9：早于此日的股票限价缺失须披露）
_STK_LIMIT_START: str = _TABLES["stk_limit"].start or "20080102"

__all__ = ["StateSynthesizer"]

#: 小标的集阈值（`states()` 双路混合）：≤ 该值走「全年过滤 + (年, 集合) 缓存」
#: （B2 逐日同集模式，重复查询免费）；> 该值走「按日切片」（B3 大集且逐日
#: 变化模式，全年过滤每次未命中 ~100 万行 dict）。64 = 撮合/再平衡典型子集上限。
_SMALL_SUBSET_MAX = 64


def _ymd_to_date(s: str | None) -> _date | None:
    """'YYYYMMDD' → `date`（R4：解析口径改用 `domain.types.parse_trading_date`）。

    `None`/空串保持返回 `None`（主库缺日期字段的既有语义不变）。
    """
    if not s:
        return None
    try:
        return parse_trading_date(s).iso
    except (TypeError, ValueError):
        return None      # 主库脏日期（位数异常/非数字）→ 按缺值处理（既有语义）


class StateSynthesizer:
    """按 (symbol, date) 合成 TradingState（懒加载 + 年度缓存）。"""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else MARKET_DATA_DIR
        # EX-4 完全体（19 号 §49）：取数**只经 core**（原 7 处直呼 `pq.read_table`）
        from btf.data.core import YearTableStore

        self._store = YearTableStore(self.root)
        # (year, wanted|None) → limit 行缓存；年 Table 单独缓存（read 一次）
        self._limit_cache: dict[
            tuple[int, frozenset[str] | None],
            dict[tuple[str, str], tuple[float | None, float | None]],
        ] = {}
        self._limit_tables: dict[int, object | None] = {}
        # ── ETF 限价通道（V3-2；M3 6.3；etf_limit 起点 2019-06-26，E3）──
        self._etf_limit_tables: dict[int, object | None] = {}
        self._etf_limit_cache: dict[
            tuple[int, frozenset[str] | None],
            dict[tuple[str, str], tuple[float | None, float | None]],
        ] = {}
        self._pre_close_cache: dict[
            tuple[int, frozenset[str] | None], dict[tuple[str, str], float],
        ] = {}
        #: E3 披露：限价回退发生日（etf_limit 未覆盖区间由 BoardRule 计算）
        self.fallback_limit_dates: set[str] = set()
        #: P2-9 披露对称：股票限价缺失日（stk_limit 起点之前）——原实现
        #: 股票分支直接落 None 且**零披露**，ETF 却有回退+登记（同一物理
        #: 约束两种待遇）；本登记让股票侧走同一披露通道（19 号 P2-9）
        self.stock_limit_missing_dates: set[str] = set()
        self._suspend_cache: dict[int, set[tuple[str, str]]] = {}
        # ── B3 十年尺度第三刀（CP1）：stk_limit **按日切片索引** ──
        # year → 排序后年表已存 `_limit_tables`；year → {ymd: (lo, hi)} 行区间
        self._limit_date_idx: dict[int, dict[str, tuple[int, int]]] = {}
        # (year, ymd) → 单日 limit 截面（月度重复查询命中：调仓日+撮合日）
        self._limit_day_cache: dict[
            tuple[int, str], dict[tuple[str, str], tuple[float | None, float | None]]
        ] = {}
        self._st_cache: list[tuple[str, _date, _date | None]] | None = None
        self._delist: dict[str, _date | None] | None = None
        # ── IO-5（P1-3a）：daily 收盘价**年表 + 按日索引**（原逐日整表读）──
        self._close_tables: dict[int, object | None] = {}
        self._close_date_idx: dict[int, dict[str, tuple[int, int]]] = {}
        self._close_day_cache: dict[
            tuple[int, str], dict[str, float]] = {}
        # ── PF-5（P1-3c）：ST 区间**事件时间线**（原逐日线性扫全部区间）──
        self._st_timeline: tuple[list[_date], list[frozenset[str]]] | None = None
        # ── PF-4（P2-7）：淘汰只在**年切换**时触发（原每 bar 全键扫描）──
        self._cache_year: int | None = None
        self._evict_calls = 0
        self._evict_scans = 0

    #: 年度缓存上限（年数）：引擎按日推进，年度缓存**常驻**在 B3 十年尺度
    #: 实测 MemoryError（10 年 × ~170 万行 dict + Arrow 表）；保留最近 N 年
    #: 即够（当年查询全命中，历史年自然淘汰——2026-09-27 CP1 性能专项）。
    _YEAR_CACHE = 2

    def _evict_year_cache(self, year: int) -> None:
        """淘汰窗口外年度缓存（内存上限）；**年切换才扫描**（PF-4 / P2-7）。

        PF-4 留痕：原实现在每次 `states()` 调用时**无条件**全键扫描 8 个
        缓存容器——2,430 日 × 全缓存键 ≈ 纯开销（年度缓存窗口不变时结果
        必为"无淘汰"）。现由 `_maybe_evict()` 在**年变化**时调用一次，
        语义不变（窗口只随 year 增大，年内重复调用不产生新淘汰）。
        """
        self._evict_scans += 1
        floor = year - self._YEAR_CACHE + 1
        for store in (self._limit_cache, self._etf_limit_cache,
                      self._pre_close_cache, self._limit_day_cache):
            stale = [key for key in store if key[0] < floor]
            for key in stale:
                del store[key]
        for table_store in (self._limit_tables, self._etf_limit_tables):
            stale = [key for key in table_store if key < floor]
            for key in stale:
                del table_store[key]
        # 日期区间索引与排序表同键同年——必须同步淘汰（索引引用排序表）
        stale_idx = [y for y in self._limit_date_idx if y < floor]
        for stale_year in stale_idx:
            del self._limit_date_idx[stale_year]
        stale_years = [y for y in self._suspend_cache if y < floor]
        for year_key in stale_years:
            del self._suspend_cache[year_key]
        # IO-5 新增缓存：年表/按日索引同年淘汰；逐日结果缓存按键前缀淘汰
        for table_store in (self._close_tables, self._close_date_idx):
            for year_key in [y for y in table_store if y < floor]:
                del table_store[year_key]
        for key in [k for k in self._close_day_cache if int(k[0]) < floor]:
            del self._close_day_cache[key]

    def _maybe_evict(self, year: int) -> None:
        """年度缓存维护入口（PF-4）：**年切换**时淘汰一次。"""
        self._evict_calls += 1
        if year != self._cache_year:
            self._evict_year_cache(year)
            self._cache_year = year

    def _limit_table(self, year: int):
        """stk_limit 年表（一次读取，B2 第四刀；年度缓存有上限）。

        EX-4：经 `core.read_file`（缺失 → None，语义与原 `path.is_file()` 守卫一致）。
        """
        if year not in self._limit_tables:
            self._limit_tables[year] = self._store.read_file(
                "stk_limit", year=year,
                columns=["ts_code", "trade_date", "up_limit", "down_limit"])
        return self._limit_tables[year]

    # ── stk_limit：(year, wanted) → {(code, ymd): (up, down)} ──
    def _limits(
        self, year: int, wanted: Sequence[str] | None = None,
    ) -> dict[tuple[str, str], tuple[float | None, float | None]]:
        """年度 limit 表查询（B2 优化：子集过滤 + (year, 集合) 结果缓存）。

        全年构建 0.6s+/年（70-175 万行）是 B2 最大热点；撮合/Rebalancer/
        ctx 路径均传 ≤40 标的子集——is_in 列过滤后仅物化命中行
        （~标的数×250 行），frozenset 结果缓存吸收月度集合重复查询；
        10 年窗口成本 ≈2s（全量口径 ~6.4s）。wanted=None（universe
        全市场口径，states(symbols=None)）保持全年构建——语义不变。
        """
        import pyarrow as pa

        key = (year, None if wanted is None else frozenset(wanted))
        if key not in self._limit_cache:
            table: dict[tuple[str, str], tuple[float | None, float | None]] = {}
            # 空集合 → 空结果：既免读年表，也规避 `pa.array([])` 的 null
            # 类型（types_mapper 缺省）与 large_string 列做 is_in 的类型冲突
            # ——引擎首日无持仓无目标时会传入空集（M3 6.3 引擎级复跑实测）。
            if key[1] is None or key[1]:
                t = self._limit_table(year)
                if t is not None and t.num_rows:
                    if key[1] is not None:
                        mask = pc.is_in(t.column("ts_code"),
                                        value_set=pa.array(sorted(key[1]),
                                                           type=t.schema.field(
                                                               "ts_code").type))
                        t = t.filter(mask)
                    if t.num_rows:
                        table = dict(zip(
                            zip(t.column("ts_code").to_pylist(),
                                t.column("trade_date").to_pylist(), strict=True),
                            zip(t.column("up_limit").to_pylist(),
                                t.column("down_limit").to_pylist(), strict=True),
                            strict=True))
            self._limit_cache[key] = table
        return self._limit_cache[key]

    # ── stk_limit 按日切片（B3 十年尺度第三刀；CP1 性能专项 2026-09-27）──
    def _limit_date_index(
        self, year: int,
    ) -> tuple[object | None, dict[str, tuple[int, int]]]:
        """年表按 trade_date 排序一次 + {ymd: (lo, hi)} 行区间索引（年度缓存）。

        旧 `_limits(year, 标的集)` 按标的过滤**全年**再物化 dict——B3 月调仓
        下标的集逐日变化 → 缓存每次未命中即 ~100 万行 to_pylist（实测
        154ms/次 × 24 次/年 ≈ 3.7s/年 ≈ 40% 总耗时）。排序 + 区间索引后，
        单日查询只 slice/to_pylist ~7000 行（~10ms）。
        """
        cached = self._limit_date_idx.get(year)
        if cached is not None:
            return self._limit_tables[year], cached
        t = self._limit_table(year)
        index: dict[str, tuple[int, int]] = {}
        if t is not None and t.num_rows:
            t = t.take(pc.sort_indices(t, sort_keys=[("trade_date", "ascending")]))
            self._limit_tables[year] = t           # 排序后表替换缓存（同 year 键）
            grouped = t.group_by("trade_date").aggregate([("ts_code", "count")])
            ordered = grouped.sort_by("trade_date")
            start = 0
            for day, count in zip(ordered.column("trade_date").to_pylist(),
                                  ordered.column("ts_code_count").to_pylist(),
                                  strict=True):
                index[day] = (start, start + count)
                start += count
        self._limit_date_idx[year] = index
        return t, index

    def _limit_day(
        self, year: int, ymd: str,
    ) -> dict[tuple[str, str], tuple[float | None, float | None]]:
        """单日 limit 截面（(year, ymd) 缓存；语义与 `_limits` 全量口径一致）。"""
        key = (year, ymd)
        rows = self._limit_day_cache.get(key)
        if rows is None:
            rows: dict[tuple[str, str], tuple[float | None, float | None]] = {}
            t, index = self._limit_date_index(year)
            span = index.get(ymd) if t is not None else None
            if span:
                lo, hi = span
                day = t.slice(lo, hi - lo)
                rows = dict(zip(
                    zip(day.column("ts_code").to_pylist(),
                        day.column("trade_date").to_pylist(), strict=True),
                    zip(day.column("up_limit").to_pylist(),
                        day.column("down_limit").to_pylist(), strict=True),
                    strict=True))
            self._limit_day_cache[key] = rows
        return rows

    # ── suspend_d：年度缓存 (code, ymd) 集合 ──
    def _suspensions(self, year: int) -> set[tuple[str, str]]:
        if year not in self._suspend_cache:
            t = self._store.read_file("suspend_d", year=year)
            s: set[tuple[str, str]] = set()
            if t is not None:
                s = set(zip(t.column("ts_code").to_pylist(),
                            t.column("trade_date").to_pylist(), strict=True))
            self._suspend_cache[year] = s
        return self._suspend_cache[year]

    # ── namechange：ST 区间（name 含 'ST'）──
    def _st_intervals(self) -> list[tuple[str, _date, _date | None]]:
        if self._st_cache is None:
            out: list[tuple[str, _date, _date | None]] = []
            # EX-4：文件清单经 `core.file_stems`（by_year 布局；目录缺失 → []）
            for stem in self._store.file_stems("namechange"):
                t = self._store.read_file("namechange", year=int(stem))
                if t is None:
                    continue
                for i in range(t.num_rows):
                    name = t.column("name")[i].as_py() or ""
                    if "ST" not in name:
                        continue
                    code = t.column("ts_code")[i].as_py()
                    start = _ymd_to_date(t.column("start_date")[i].as_py())
                    end = _ymd_to_date(t.column("end_date")[i].as_py())
                    if start is not None:
                        out.append((code, start, end))
            self._st_cache = out
        return self._st_cache

    def _st_timeline_index(
        self,
    ) -> tuple[list[_date], list[frozenset[str]]]:
        """ST 区间 → **事件时间线**（PF-5/P1-3c）：O(log n) 单日查询。

        原实现每次查询线性扫全部区间（≈千级 × 每 bar）；今按区间**端点事件**
        （+1 入 / −1 出）排序后做一次前缀累积，得到「各事件日之后的在册 ST 集合」
        快照列表。查询 = `bisect_right(事件日, d) − 1` 取快照（O(log n)），
        语义与原集合推导式**逐位一致**（同一 (start ≤ d < end) 谓词）。
        内存：事件日 ≈2×区间数（千级）× 小 frozenset（当日 ST 数十）——可忽略。
        """
        intervals = self._st_intervals()
        events: dict[_date, list[tuple[int, str]]] = {}
        for code, start, end in intervals:
            events.setdefault(start, []).append((1, code))
            if end is not None:
                events.setdefault(end, []).append((-1, code))
        days = sorted(events)
        active: set[str] = set()
        snaps: list[frozenset[str]] = []
        for day in days:
            for delta, code in events[day]:
                if delta > 0:
                    active.add(code)
                else:
                    active.discard(code)
            snaps.append(frozenset(active))
        self._st_timeline = (days, snaps)
        return self._st_timeline

    def _st_codes_on(self, d: _date) -> set[str]:
        """某日在册 ST 集合（PF-5：事件时间线 + 二分，原为逐日线性扫）。"""
        from bisect import bisect_right

        if self._st_timeline is None:
            self._st_timeline = self._st_timeline_index()
        days, snaps = self._st_timeline
        idx = bisect_right(days, d) - 1        # 最后一个 ≤ d 的事件日
        return set(snaps[idx]) if idx >= 0 else set()

    # ── 公开接口（批 9 / DD-1：给数据不变量校验 CC-2 供 ST 名单）──
    def st_source(self) -> str:
        """ST 名单来源标记（铁律新 16：**取不到须披露，不得静默**）。

        返回 `"namechange"`（源表存在，可判定）或 `"unavailable"`（源表缺失/
        读取失败 ⇒ CC-2 **只按板块上限**判定，会**漏检** ST 股 5%~10% 的越界）。
        """
        try:
            intervals = self._st_intervals()
        except Exception:                      # 源表缺失/损坏
            return "unavailable"
        if not intervals:
            # 空区间可能是"确实无 ST"，也可能是源表缺失——以**文件存在性**判定
            base = self.root / "namechange"
            exists = (base.is_dir() and any(base.glob("*.parquet")))
            return "namechange" if exists else "unavailable"
        return "namechange"

    def st_pairs(self, start_ymd: str, end_ymd: str,
                 symbols: Sequence[str] | None = None, *,
                 cal: Collection[str]) -> set[tuple[str, str]]:
        """区间内「(symbol, ymd) 当日为 ST」集合（CC-2 的 5% 上限依据）。

        为什么是公开接口（§44.2 形态 A）：CC-2 的 ST 分支此前**从未接线**——
        `run_checks(st_symbols=None)` 恒按板块上限（主板 10%）⇒ ST 股
        5%~10% 的越界**漏检**（P2-NEW-8）。本方法把"最后一根线"补齐：

        - **日期维度须与 CC-2 同源**：`cal` 必填（调用方传 `trade_cal` 交易日
          序列），避免"有 ST 标记却非交易日"导致日对齐错位；
        - **抽样子集对齐**：`symbols` 给定时只保留该子集（与校验集一致、省内存）；
        - **成本**：`_st_codes_on` 走事件时间线 + 二分（O(log n)），区间遍历为
          O(交易日 × log)，装配期**只算一次**。
        """
        if not cal:
            raise ValueError(
                "st_pairs 须传入 cal（交易日序列，与 CC-2 的 trade_cal 同源）")
        want = set(symbols) if symbols is not None else None
        out: set[tuple[str, str]] = set()
        for day in cal:
            if day < start_ymd or day > end_ymd:
                continue
            for code in self._st_codes_on(_ymd_to_date(day)):
                if want is None or code in want:
                    out.add((code, day))
        return out

    # ── stock_basic：退市日期表 ──
    def _delist_map(self) -> dict[str, _date | None]:
        if self._delist is None:
            out: dict[str, _date | None] = {}
            # EX-4：metadata 布局（`root/metadata/stock_basic.parquet`）经 core 解析
            t = self._store.read_file("stock_basic")
            if t is not None:
                codes = t.column("ts_code").to_pylist()
                delists = t.column("delist_date").to_pylist()
                for i in range(t.num_rows):
                    out[codes[i]] = _ymd_to_date(delists[i])
            self._delist = out
        return self._delist

    # ── daily：某日收盘价（涨跌停触及判定）──
    def _close_date_index(
        self, year: int,
    ) -> tuple[object | None, dict[str, tuple[int, int]]]:
        """daily 年表按 trade_date 排序一次 + {ymd: (lo, hi)} 行区间（IO-5）。

        与 `_limit_date_index` 同构（B3 第三刀已验证的口径）：单日查询只
        slice/to_pylist ≈ 当日行数，而非逐日整表读。
        """
        cached = self._close_date_idx.get(year)
        if cached is not None:
            return self._close_tables[year], cached
        t = self._close_table(year)
        index: dict[str, tuple[int, int]] = {}
        if t is not None and t.num_rows:
            t = t.take(pc.sort_indices(t, sort_keys=[("trade_date", "ascending")]))
            self._close_tables[year] = t        # 排序后表替换缓存（同 year 键）
            grouped = t.group_by("trade_date").aggregate([("ts_code", "count")])
            ordered = grouped.sort_by("trade_date")
            start = 0
            for day, count in zip(ordered.column("trade_date").to_pylist(),
                                  ordered.column("ts_code_count").to_pylist(),
                                  strict=True):
                index[day] = (start, start + count)
                start += count
        self._close_date_idx[year] = index
        return t, index

    def _close_table(self, year: int):
        """daily 年表（ts_code/trade_date/close）——年装载一次（IO-5/P1-3a）。"""
        if year not in self._close_tables:
            self._close_tables[year] = self._store.read_file(
                "daily", year=year, columns=["ts_code", "trade_date", "close"])
        return self._close_tables[year]

    def _closes_on(self, ymd: str) -> dict[str, float]:
        """某日收盘价截面（(year, ymd) 缓存；原实现**逐日整表读** → IO-5）。"""
        year = int(ymd[:4])
        key = (year, ymd)
        rows = self._close_day_cache.get(key)
        if rows is None:
            t, index = self._close_date_index(year)
            span = index.get(ymd) if t is not None else None
            rows = {}
            if span:
                lo, hi = span
                day = t.slice(lo, hi - lo)
                rows = dict(zip(day.column("ts_code").to_pylist(),
                                day.column("close").to_pylist(), strict=True))
            self._close_day_cache[key] = rows
        return rows

    # ── etf_limit：ETF 涨跌停价（V3-2；M3 6.3；起点 2019-06-26，E3）──
    def _etf_limit_table(self, year: int):
        """etf_limit 年 Table（read_table 一次）。"""
        if year not in self._etf_limit_tables:
            self._etf_limit_tables[year] = self._store.read_file(
                "etf_limit", year=year,
                columns=["ts_code", "trade_date", "up_limit", "down_limit"])
        return self._etf_limit_tables[year]

    def _etf_limits(
        self, year: int, wanted: Sequence[str] | None = None,
    ) -> dict[tuple[str, str], tuple[float | None, float | None]]:
        """etf_limit 查询（(year, 标的集合) 结果缓存；口径同 `_limits`）。"""
        import pyarrow as pa

        key = (year, None if wanted is None else frozenset(wanted))
        if key not in self._etf_limit_cache:
            table: dict[tuple[str, str], tuple[float | None, float | None]] = {}
            if key[1] is None or key[1]:        # 空集合 → 空结果（同上）
                t = self._etf_limit_table(year)
                if t is not None and t.num_rows:
                    if key[1] is not None:
                        t = t.filter(pc.is_in(
                            t.column("ts_code"),
                            value_set=pa.array(
                                sorted(key[1]),
                                type=t.schema.field("ts_code").type)))
                    if t.num_rows:
                        table = dict(zip(
                            zip(t.column("ts_code").to_pylist(),
                                t.column("trade_date").to_pylist(), strict=True),
                            zip(t.column("up_limit").to_pylist(),
                                t.column("down_limit").to_pylist(), strict=True),
                            strict=True))
            self._etf_limit_cache[key] = table
        return self._etf_limit_cache[key]

    def _etf_pre_closes(
        self, year: int, wanted: Sequence[str],
    ) -> dict[tuple[str, str], float]:
        """fund_daily 的 pre_close（限价回退输入；按 (year, 集合) 缓存）。"""
        import pyarrow as pa

        key = (year, frozenset(wanted))
        if key not in self._pre_close_cache:
            table: dict[tuple[str, str], float] = {}
            t = (self._store.read_file(
                "fund_daily", year=year,
                columns=["ts_code", "trade_date", "pre_close"])
                if wanted else None)            # 空集合 → 空结果（同上）
            if t is not None:
                t = t.filter(pc.is_in(
                    t.column("ts_code"),
                    value_set=pa.array(sorted(wanted),
                                       type=t.schema.field("ts_code").type)))
                table = {
                    (code, day): float(value)
                    for code, day, value in zip(
                        t.column("ts_code").to_pylist(),
                        t.column("trade_date").to_pylist(),
                        t.column("pre_close").to_pylist(), strict=True)
                    if value is not None
                }
            self._pre_close_cache[key] = table
        return self._pre_close_cache[key]

    def _etf_limit_fallback(
        self, code: str, ymd: str, year: int, symbols: Sequence[str] | None,
    ) -> tuple[float | None, float | None]:
        """E3 回退：etf_limit 未覆盖（起点之前）→ BoardRule 涨跌幅依 pre_close 计算。

        仅对**显式标的集合**生效（全市场口径不做回退，避免整年 fund_daily
        读入）；发生日登记 `fallback_limit_dates` 供报告披露。
        """
        if symbols is None:
            return None, None
        pre = self._etf_pre_closes(year, symbols).get((code, ymd))
        if pre is None or pre <= 0:
            return None, None
        pct = rule_for(Instrument(code, AssetClass.ETF)).limit_up_pct or 0.10
        self.fallback_limit_dates.add(ymd)
        return round(pre * (1 + pct), 2), round(pre * (1 - pct), 2)

    # ── 降级披露（E3：报告/CLI 消费）──
    def degraded_notes(self) -> tuple[str, ...]:
        """降级事件清单（ETF 限价回退 + 股票限价缺失；无降级 → 空）。

        19 号 P2-9：股票/ETF 同一物理约束（限价数据缺失）的披露**对称化**。
        """
        notes: list[str] = []
        if self.fallback_limit_dates:
            days = sorted(self.fallback_limit_dates)
            notes.append(
                "ETF 限价回退（etf_limit 起点 2019-06-26 之前）："
                f"{len(days)} 个交易日（{days[0]}–{days[-1]}）由 BoardRule ±10% 计算")
        if self.stock_limit_missing_dates:
            days = sorted(self.stock_limit_missing_dates)
            notes.append(
                f"股票涨跌停缺失（stk_limit 起点 {_STK_LIMIT_START} 之前）："
                f"{len(days)} 个交易日（{days[0]}–{days[-1]}）——"
                "涨跌停约束失效，成交偏乐观（19 号 P1-11/P2-9）")
        return tuple(notes)

    # ── 公共 API ──
    def states(
        self, date: TradingDate, symbols: Sequence[str] | None = None,
        *, with_touch_flags: bool = True,
    ) -> Mapping[str, TradingState]:
        """某交易日状态面板截面。

        symbols=None → 当日有涨跌停价数据的全市场（ stk_limit 口径，2008+）；
        传入子集 → 仅合成子集（未在 stk_limit 的标的涨跌停价为 None）。
        with_touch_flags=False → 跳过收盘触板标记（is_limit_up/down 置
        False）——撮合路径优化（B2：触板判定需读 daily 年表，月调仓
        120 执行日×~0.15s≈18s 不可行；撮合只需 limit 价/suspended/delisted）。
        """
        ymd = date.to_ymd()
        year = int(ymd[:4])
        d = date.iso

        self._maybe_evict(year)               # 年度缓存上限（PF-4：年切换才扫描）
        if symbols is None:
            # 全市场口径：当日全量切片（B3 十年第三刀；~10ms/日）
            limits = self._limit_day(year, ymd)
        elif len(symbols) <= _SMALL_SUBSET_MAX:
            # 小标的集（B2 单/少标的逐日同集）：全年过滤 + (年, 集合) 缓存——
            # 首次 ~30ms/年，其后免费。按日切片在此模式是回归（逐日重建
            # 7000 行切片，B2 实测 15.25s>15s 预算）→ 双路混合。
            limits = self._limits(year, symbols)
        else:
            # 大标的集且逐日变化（B3 月调仓 ~3000+）：按日切片（全年过滤
            # 每次未命中即 ~100 万行 dict）
            day_limits = self._limit_day(year, ymd)
            wanted = set(symbols)
            limits = {key: value for key, value in day_limits.items()
                      if key[0] in wanted}
        suspended = self._suspensions(year)
        st_codes = self._st_codes_on(d)
        delist = self._delist_map()
        closes = self._closes_on(ymd) if with_touch_flags else {}

        universe: set[str]
        if symbols is not None:
            universe = set(symbols)
        else:
            universe = {code for (code, day) in limits if day == ymd}
            universe |= {code for (code, day) in suspended if day == ymd}

        etf_limits: (dict[tuple[str, str], tuple[float | None, float | None]]
                     | None) = None
        out: dict[str, TradingState] = {}
        for code in sorted(universe):
            up, down = limits.get((code, ymd), (None, None))
            is_stock = daily_table_of(code) == "daily"
            if up is None and down is None and not is_stock:
                if etf_limits is None:            # 懒加载：无 ETF 标的时零成本
                    etf_limits = self._etf_limits(year, symbols)
                up, down = etf_limits.get((code, ymd), (None, None))
                if up is None and down is None:
                    up, down = self._etf_limit_fallback(code, ymd, year, symbols)
            elif (up is None and down is None and is_stock
                    and ymd < _STK_LIMIT_START):
                # P2-9 披露对称：股票限价缺失登记（原实现零披露）
                self.stock_limit_missing_dates.add(ymd)
            close = closes.get(code)
            out[code] = TradingState(
                symbol=code,
                date=date,
                limit_up_price=up,
                limit_down_price=down,
                is_suspended=(code, ymd) in suspended,
                is_st=code in st_codes,
                is_delisted=(delist.get(code) is not None and delist[code] <= d),
                is_limit_up=(touch_limit_up(close, up)
                             if close is not None else False),
                is_limit_down=(touch_limit_down(close, down)
                               if close is not None else False),
            )
        return out
