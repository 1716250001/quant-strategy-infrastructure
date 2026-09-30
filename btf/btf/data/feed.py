# -*- coding: utf-8 -*-
"""DataFeed 协议 + TushareParquetFeed 原型（04 §8.3.1，PoC-1 任务 1.4/1.5）。

职责边界：
    - 协议（S1）返回领域对象（Bar 等）——**单位换算在此层完成**（05 §9.4 契约：
      vol 手→股 ×100、amount 千元→元 ×1000），消费侧拿到即契约单位；
    - 只读、可重复调用（同参数同结果）、无副作用；
    - 停牌股当日不在 bars 截面 map 中（库内口径：停牌日 daily 无行）。
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from btf.config.paths import MARKET_DATA_DIR
from btf.data.tables_meta import daily_table_of
from btf.domain.action import CorporateAction
from btf.domain.cache import BoundedDict
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.market import Bar
from btf.domain.types import AssetClass, Instrument, TradingDate

#: dividend 回看窗口（年）：年文件按**方案年度**归档，ex_date 最长滞后实测 **5 年**
#: （IO-3/P1-8，19 号附录 D.3；实测分布 {0:5448, 1:54083, 2:47, 3:3, 4:1, 5:2}）。
#: 守卫：`tests/unit/test_dividend_lag_guard.py` 全表重测——出现 >5 年即红。
_DIV_MAX_LAG_YEARS = 5

#: corporate_actions 进程内 memo（IO-3）：键 (root, start, end) → 行动元组。
#: 网格搜索逐组合调用同一区间（原每组合全表读 ≈0.7s）；主库只读 → 结果稳定。
_CORPORATE_ACTIONS_CACHE: BoundedDict[tuple[str, str, str], tuple] = BoundedDict(
    maxsize=32, name="feed.corporate_actions")


@runtime_checkable
class DataFeed(Protocol):
    """数据源协议（04 §8.3.1；M1 任务 4.1 定稿）。

    实现纪律：只读、可重复调用（同参数同结果）、无副作用；返回
    Sequence/Iterator 而非 DataFrame（领域层不依赖 pandas）。

    与 04 §8.3.1 原签名的已实证偏差（留痕供 diff 评审）：
        - corporate_actions(start, end)：全市场区间口径（引擎步骤①单次
          装载，PoC-3 交付形态）——doc 的 per-symbol 变体由调用方过滤；
        - universe() 只承担**静态身份**（上市/退市窗口内股票清单），
          ST/停牌**动态标记**走 trading_states()——状态合成器由**装配根注入**
          （`TushareParquetFeed.attach_state_provider`；R4 已反转 `feed→state`
          依赖边）；ST 历史取自 `namechange`（stock_basic 现名无历史口径，
          doc"含 ST/停牌标记"按此职责切分落地）；
        - history() 返回 list[Bar] 全字段（fields 参数预留列裁剪）。
    """

    def universe(self, date: TradingDate) -> list[Instrument]:
        """截至 date 的可交易宇宙（含已退市=历史口径；静态身份）。"""
        ...

    def bars_of(self, symbol: str, start: TradingDate, end: TradingDate) -> list[Bar]: ...

    def history(self, symbol: str, end: TradingDate,
                fields: Sequence[str] | None = None, n_bars: int = 1) -> list[Bar]:
        """截至 end（含）最近 n_bars 个 bar（升序；研究/预计算用）。

        fields 预留（列裁剪优化，PoC 全字段返回）；重负载路径应使用
        StrategyContext.history（engine 生命周期内 per-symbol 缓存）。
        """
        ...

    def bars(
        self, symbols: Sequence[str] | None, start: TradingDate, end: TradingDate
    ) -> Iterator[tuple[TradingDate, Mapping[str, Bar]]]: ...

    def trading_states(
        self, date: TradingDate, symbols: Sequence[str] | None = None,
        *, with_touch_flags: bool = True,
    ) -> Mapping[str, object]:
        """with_touch_flags=False：跳过收盘触板标记合成（撮合路径优化，
        B2 依据：触板判定需读 daily 年表——月调仓 120 执行日×~0.15s ≈ 18s
        不可行；撮合只需 limit 价/suspended/delisted）。"""
        ...

    def corporate_actions(
        self, start: TradingDate, end: TradingDate
    ) -> list[CorporateAction]:
        """区间全市场实施态行动（PoC-3 任务 3.2；引擎步骤①装载源）。

        返回升序（ex_date, symbol）；仅 div_proc='实施' 行进入引擎
        （05 §9.6：预案/股东大会等状态含未落定参数）。pay_date 缺失
        回退语义（E5）由引擎落地，此处保留 None 原样传递。
        """
        ...


def _to_bar(row: Mapping, conversions: Mapping[str, float]) -> Bar | None:
    """原始行 → Bar（单位换算单点）。row 字段为库内原始口径。

    坏行语义（05 §20.4 质量门）：OHLC 任一为 None → 返回 None（剔除），
    由调用方计数登记——停牌日残行/异常行不得进入引擎。
    """
    essentials = ("open", "high", "low", "close", "pre_close")
    if any(row.get(k) is None for k in essentials):
        return None
    vol_f = conversions.get("vol", 1.0)
    amt_f = conversions.get("amount", 1.0)
    return Bar(
        symbol=row["ts_code"],
        date=TradingDate.from_ymd(row["trade_date"]),
        open=float(row["open"]), high=float(row["high"]),
        low=float(row["low"]), close=float(row["close"]),
        pre_close=float(row["pre_close"]),
        volume=(float(row["vol"]) if row.get("vol") is not None else 0.0) * vol_f,
        amount=(float(row["amount"]) if row.get("amount") is not None else 0.0) * amt_f,
    )


class _LazyCrossSection(Mapping):
    """懒截面：列式行区间 [lo, hi) → 按需构造 Bar，**不整截面物化**。

    B1/B2 性能依据（PoC-1 CP1 剖析 + 任务 2.8）：全量物化 950 万 Bar ≈ 22s
    （B1 物化段证据），事件循环禁止触发；同日区间 ts_code 升序
    （parquet_reader 单键稳定排序等价性验证）→ 单标的 bisect（O(log n)），
    keys()/__len__ 走列切片（不构造 Bar，天然 ts_code 升序=确定性）。
    语义注记：坏行标的仍可能出现在 keys()（切片不含值校验），但
    __getitem__/get 视为缺失——撮合语义正确（SUSPENDED 拒单），
    摘要审计语义可接受。
    """

    __slots__ = ("_built", "_closes", "_cols", "_conv", "_hi", "_lo", "_wanted")

    def __init__(
        self,
        cols: Mapping[str, list],
        lo: int,
        hi: int,
        conversions: Mapping[str, float],
        wanted: set[str] | None,
    ):
        self._cols = cols
        self._lo, self._hi = lo, hi
        self._conv = conversions
        self._wanted = wanted
        self._built: dict[str, Bar] | None = None
        self._closes: dict[str, float] | None = None

    def closes(self, wanted: set[str] | None = None) -> dict[str, float]:
        """当日收盘价轻量视图 {symbol: close}（B3 十年尺度第二刀，CP1）。

        引擎 mark 步骤只需 **close 一列**：全 Bar 物化 ≈8M 个对象/十年
        （~34s）是 B3 最大热点之二；本视图免 Bar 构造（单列一次扫描）。
        坏行语义与 `__getitem__`/`_build` **同口径**（任一 OHLC/pre_close
        缺失 → 缺席），mark 结果逐位一致。

        `wanted`（19 号审查 PF-3/P2-6）：只取所需标的——全市场口径原实现
        每次建全截面 dict（十年 ≈9.7e6 次无谓写）；给定持仓集合后按需产出
        （扫描成本同，dict 写降为 O(P)）。已物化时直接筛（零重复扫描）。
        """
        if wanted is None:
            if self._closes is None:
                c = self._cols
                codes = c["ts_code"]
                opens, highs = c["open"], c["high"]
                lows, closes_col, pres = c["low"], c["close"], c["pre_close"]
                flt = self._wanted
                out: dict[str, float] = {}
                for i in range(self._lo, self._hi):
                    code = codes[i]
                    if flt is not None and code not in flt:
                        continue
                    if (opens[i] is None or highs[i] is None
                            or lows[i] is None or closes_col[i] is None
                            or pres[i] is None):
                        continue                 # 坏行剔除（05 §20.4）
                    out[code] = closes_col[i]
                self._closes = out
            return self._closes
        if self._closes is not None:             # 已物化 → 直接筛
            return {s: self._closes[s] for s in wanted if s in self._closes}
        c = self._cols
        codes = c["ts_code"]
        opens, highs = c["open"], c["high"]
        lows, closes_col, pres = c["low"], c["close"], c["pre_close"]
        subset = set(wanted)
        if self._wanted is not None:
            subset &= self._wanted
        picked: dict[str, float] = {}
        for i in range(self._lo, self._hi):
            code = codes[i]
            if code not in subset:
                continue
            if (opens[i] is None or highs[i] is None or lows[i] is None
                    or closes_col[i] is None or pres[i] is None):
                continue
            picked[code] = closes_col[i]
        return picked

    def _build(self) -> dict[str, Bar]:
        if self._built is None:
            c = self._cols
            codes, dates = c["ts_code"], c["trade_date"]
            opens, highs = c["open"], c["high"]
            lows, closes, pres = c["low"], c["close"], c["pre_close"]
            vols, amounts = c["vol"], c["amount"]
            vol_f = self._conv.get("vol", 1.0)
            amt_f = self._conv.get("amount", 1.0)
            td = None
            out: dict[str, Bar] = {}
            for i in range(self._lo, self._hi):
                code = codes[i]
                if self._wanted is not None and code not in self._wanted:
                    continue
                o, h = opens[i], highs[i]
                if o is None or h is None:  # 坏行剔除（05 §20.4）
                    continue
                lo, cl, p = lows[i], closes[i], pres[i]
                if lo is None or cl is None or p is None:
                    continue
                if td is None:
                    td = TradingDate.from_ymd(dates[self._lo])
                v = vols[i]
                a = amounts[i]
                out[code] = Bar(
                    symbol=code, date=td,
                    open=o, high=h, low=lo, close=cl, pre_close=p,
                    volume=(v if v is not None else 0.0) * vol_f,
                    amount=(a if a is not None else 0.0) * amt_f,
                )
            self._built = out
        return self._built

    def _locate(self, key: str) -> int:
        """bisect 定位 key 行号（同日 ts_code 升序=B1 等价性证据）；未命中 → -1。"""
        from bisect import bisect_left

        codes = self._cols["ts_code"]
        i = bisect_left(codes, key, self._lo, self._hi)
        return i if i < self._hi and codes[i] == key else -1

    def _bar_at(self, i: int) -> Bar | None:
        """行号 → Bar（坏行 None，05 §20.4）。"""
        c = self._cols
        o, h = c["open"][i], c["high"][i]
        lo, cl, p = c["low"][i], c["close"][i], c["pre_close"][i]
        if o is None or h is None or lo is None or cl is None or p is None:
            return None
        v, a = c["vol"][i], c["amount"][i]
        return Bar(
            symbol=c["ts_code"][i], date=TradingDate.from_ymd(c["trade_date"][i]),
            open=o, high=h, low=lo, close=cl, pre_close=p,
            volume=(v if v is not None else 0.0) * self._conv.get("vol", 1.0),
            amount=(a if a is not None else 0.0) * self._conv.get("amount", 1000.0),
        )

    def __getitem__(self, key: str) -> Bar:
        if self._wanted is not None and key not in self._wanted:
            raise KeyError(key)
        i = self._locate(key)
        if i < 0:
            raise KeyError(key)
        bar = self._bar_at(i)
        if bar is None:
            raise KeyError(key)       # 坏行=截面缺席
        return bar

    def keys(self):  # Mapping.keys 覆盖：列切片，不物化 Bar
        codes = self._cols["ts_code"][self._lo:self._hi]
        if self._wanted is None:
            return codes
        return [c for c in codes if c in self._wanted]

    def __iter__(self):
        return iter(self.keys())

    def __len__(self) -> int:
        # 快速路径（B2）：wanted=None 时 keys()=列切片，长度即行区间宽
        # （坏行语义不变——keys 本就不校验值，见类 docstring）
        if self._wanted is None:
            return self._hi - self._lo
        return len(self.keys())

    def __contains__(self, key: object) -> bool:
        return self._locate(key) >= 0


def _board_of(ts_code: str, market: str | None) -> str:
    """板块判定：market 列优先，缺列按代码前缀推断（MVP 口径）。"""
    if market:
        return {
            "主板": "main", "中小板": "main", "创业板": "gem",
            "科创板": "star", "北交所": "bse",
        }.get(market, "main")
    prefix = ts_code[:2]
    if prefix in {"60", "00"}:
        return "main"
    if prefix == "68":
        return "star"
    if prefix == "30":
        return "gem"
    return "bse"                       # 43/83/87/92 等


class TushareParquetFeed:
    """Tushare 主库 Parquet 直读 Feed（ADR-2 直读路径）。

    日线表选择：股票用 daily，ETF 用 fund_daily——按标的代码后缀与池归属由
    调用方声明（MVP 简化：daily_table 参数，默认 "daily"；ETF 场景传 fund_daily）。
    """

    contract_version = CONTRACT_VERSION

    def __init__(self, root: Path | None = None, *, state_provider: Any = None):
        """`state_provider`：状态面板合成器（`StateSynthesizer`），**构造期注入**。

        R4（19 号 §47.4 余项 ①）：本模块**不再 import `btf.data.state`**
        （原为契约新 8「data 域模块互不依赖」的唯一豁免边）。装配根
        （`BTFRuntime.build`）创建 Feed 后立即 `attach_state_provider(...)`；
        直接构造的自定义场景（测试/工具）请显式传参：:

            TushareParquetFeed(root, state_provider=StateSynthesizer(root))

        未注入而调用 `states` / `trading_states` → **显式报错**（不静默降级：
        状态面板是撮合与风控的唯一可交易性依据，缺它必须失败）。
        """
        self.root = Path(root) if root else MARKET_DATA_DIR
        from btf.data.tables_meta import TABLES

        self._meta = TABLES
        self._daily_conversions = {
            "vol": 100.0, "amount": 1000.0,
        }
        self._state = state_provider
        # EX-4 完全体（19 号 §49）：取数**只经 core**（原 6 处直呼 parquet_reader）
        from btf.data.core import YearTableStore

        self._store = YearTableStore(self.root)
        #: stock_basic 原始行缓存（IO-6 / P1-3b）：整表读一次，逐日 universe() 免费
        self._stock_basic_rows_cache: list[dict] | None = None

    def attach_state_provider(self, provider: Any) -> None:
        """注入状态面板合成器（装配根调用；幂等——后注入覆盖前者）。"""
        self._state = provider

    def _require_state(self) -> Any:
        """取状态合成器；未注入 → 报错并给出可执行的修复指引（铁律新 16）。"""
        if self._state is None:
            raise RuntimeError(
                "状态面板未注入：`TushareParquetFeed.states/trading_states` 需要 "
                "`StateSynthesizer`。经 `BTFRuntime` 运行会自动注入；直接构造请传 "
                "`state_provider=StateSynthesizer(root)`（R4 已反转 feed→state 依赖边）")
        return self._state

    @property
    def conversions(self) -> dict[str, float]:
        """单位换算系数（vol 手→股、amount 千元→元；V3-1 混合表复用同一口径）。"""
        return self._daily_conversions

    @property
    def states(self):
        """状态面板合成器（`degraded_notes()` 等披露信息的来源）。"""
        return self._require_state()

    # ── 宇宙（静态身份；M1 任务 4.1）──
    def universe(self, date: TradingDate) -> list[Instrument]:
        """截至 date 的股票清单（含已退市；Instrument 一等公民口径）。

        来源 stock_basic（metadata 表，L/D/P 状态齐全）：list_date ≤
        date ≤ delist_date（delist 空 = 在市）。板块优先 market 列
        （主板/中小板→main，创业板→gem，科创板→star，北交所→bse）；
        主库实测 stock_basic 无 market 列（ts_code/symbol/name/area/
        industry/list_date/delist_date/list_status）——读取前先
        peek_columns 求交集（列缺失直接传 pyarrow 会 ArrowInvalid，
        M1 实测教训），缺失时按代码前缀推断（_board_of）。MVP 范围=
        股票（stock_basic）；ETF 池经 fund_daily 声明式传入（M3 任务
        6.3 ETF BoardRule）。ST/停牌动态标记不在本方法（协议 docstring
        职责切分）。
        """
        ymd = date.to_ymd()

        def alive(listed: str | None, delisted: str | None) -> bool:
            if listed and listed > ymd:
                return False
            return not (delisted and delisted < ymd)

        return self._stock_basic_instruments(alive)

    def universe_span(self, start: TradingDate,
                      end: TradingDate) -> list[Instrument]:
        """**[start, end] 期间宇宙并集**（19 号 §20.3 / P0-NEW-4）。

        为什么需要：`universe(date)` 本身**逐日动态**（`list_date ≤ d ≤
        delist_date`），但 `source=all` 原实现只在**起点**求值一次 → 期间新
        上市标的（十年实测 **2,623 只 = 期末宇宙的 48.3%**）**从头到尾不可见**：
        策略选股走基本面库可正常选出，但 `ctx.data`（截面来自
        `bars(cross_section)`）不含新股 → **选得出、买不到，且零报错零拒单
        零披露**；与指数对比时指数含新股而回测不含 → 相对收益/信息比率系统性
        失真。区间越长越严重。

        口径（并集的闭式）：`list_date ≤ end ∧ (delist_date 空 ∨
        delist_date ≥ start)` —— 等价于 ∃d∈[start,end] 使该标的在市；**一次**
        stock_basic 读（非逐日调用；`universe()` 每次整表读，逐日调用不可行）。

        **为何并集优于"逐日动态"**（19 号 §22.4 实测留痕，审查方自我纠错）：
            ① 性能：并集 vs 逐日动态，十年实测仅差 **57s（3%）**——pyarrow
               谓词下推 + 列裁剪下**标的数量不是瓶颈**（文件扫描/IO 才是），
               无效请求不产生 `Bar` 对象、内存代价可忽略；省 3% 却要引入
               逐日求宇宙 + `stock_basic` 缓存的新状态，不划算。
            ② **抗元数据噪声**：实测 `20160104` 并集多命中 **43 只**——即存在
               「`stock_basic` 的 list/delist 与 `daily` 行情表不一致」的标的；
               逐日口径依赖元数据精确性会把它们**误排除**，并集不依赖 →
               宽松超集反而更稳健。
        """
        lo, hi = start.to_ymd(), end.to_ymd()

        def alive(listed: str | None, delisted: str | None) -> bool:
            if listed and listed > hi:
                return False
            return not (delisted and delisted < lo)

        return self._stock_basic_instruments(alive)

    def _stock_basic_rows(self) -> list[dict]:
        """stock_basic **原始行**（实例级缓存，IO-6 / P1-3b）。

        原实现：`universe()` 每次调用 `peek_columns` + `read_full` + `to_pylist`
        整表读（docstring 自述"逐日调用不可行"）——而 `universe(date)` 的
        "截至 date"过滤只依赖 `list_date`/`delist_date` **列值**（纯函数），
        故整表读一次即可；同实例重复查询（逐日/多区间）零 IO。
        """
        if self._stock_basic_rows_cache is None:
            want = ["ts_code", "name", "market", "list_date", "delist_date"]
            have = set(self._store.peek_columns("stock_basic"))
            cols = [c for c in want if c in have] or ["ts_code"]
            t = self._store.read_full("stock_basic", columns=cols)
            self._stock_basic_rows_cache = t.to_pylist()
        return self._stock_basic_rows_cache

    def _stock_basic_instruments(
        self, alive,      # (list_date, delist_date) -> bool
    ) -> list[Instrument]:
        """stock_basic → Instrument 列表（`universe` / `universe_span` 共用）。

        `alive` 为在市判定闭包——两方法的唯一差异即此谓词（单日 vs 期间并集），
        其余（列缺失容错、板块推断、排序）逐字共用；整表读经 `_stock_basic_rows`
        实例级缓存（IO-6）。
        """
        out: list[Instrument] = []
        for r in self._stock_basic_rows():
            listed, delisted = r.get("list_date"), r.get("delist_date")
            if not alive(listed, delisted):
                continue
            code = r["ts_code"]
            out.append(Instrument(
                symbol=code,
                asset_class=AssetClass.STOCK,
                name=r.get("name") or "",
                board=_board_of(code, r.get("market")),
                lot_size=100,
                list_date=(TradingDate.from_ymd(listed) if listed else None),
                delist_date=(TradingDate.from_ymd(delisted) if delisted else None),
            ))
        out.sort(key=lambda i: i.symbol)
        return out

    def history(self, symbol: str, end: TradingDate,
                fields: Sequence[str] | None = None, n_bars: int = 1) -> list[Bar]:
        """截至 end 最近 n_bars 个 bar（协议口径；无 engine 生命周期缓存，
        重负载走 StrategyContext.history）。"""
        bars = self.bars_of(symbol, TradingDate.from_ymd("19901219"), end)
        return bars[-n_bars:] if n_bars > 0 else []

    # ── 单标的时序 ──
    def bars_of(
        self,
        symbol: str,
        start: TradingDate,
        end: TradingDate,
        *,
        daily_table: str = "daily",
    ) -> list[Bar]:
        t = self._store.read_codes(
            daily_table, [symbol],
            start.to_ymd(), end.to_ymd(),
            columns=["ts_code", "trade_date", "open", "high", "low",
                     "close", "pre_close", "vol", "amount"],
        )
        # 无行情（未上市/退市/区间外）→ 空表无列，直接返回 []（不得进
        # sort_by——`read_codes` 空结果返回 `pa.table({})`，实测 ArrowInvalid）
        if not t.num_rows or "trade_date" not in t.column_names:
            return []
        t = t.sort_by([("trade_date", "ascending")])
        cols = {n: t.column(n).to_pylist() for n in t.column_names}
        bars: list[Bar] = []
        n_bad = 0
        for i in range(t.num_rows):
            bar = _to_bar({n: cols[n][i] for n in t.column_names}, self._daily_conversions)
            if bar is None:
                n_bad += 1
                continue
            bars.append(bar)
        if n_bad:
            import logging

            logging.getLogger(__name__).warning(
                "bars_of(%s): 剔除坏行 %d 条（OHLC 空值）", symbol, n_bad
            )
        return bars

    # ── 截面迭代（事件内核输入形态；懒转换热路径）──
    def bars(
        self,
        symbols: Sequence[str] | None,
        start: TradingDate,
        end: TradingDate,
        *,
        daily_table: str = "daily",
    ) -> Iterator[tuple[TradingDate, Mapping[str, Bar]]]:
        wanted = set(symbols) if symbols is not None else None
        for ymd, cols, lo, hi in self._store.iter_day_slices(
            daily_table, start.to_ymd(), end.to_ymd(),
            columns=["ts_code", "trade_date", "open", "high", "low",
                     "close", "pre_close", "vol", "amount"],
            symbols=list(wanted) if wanted is not None else None,
        ):
            yield TradingDate.from_ymd(ymd), _LazyCrossSection(
                cols, lo, hi, self._daily_conversions, wanted
            )

    # ── 状态面板（1.6 交付；2.5 撮合路径加 with_touch_flags 裁剪）──
    def trading_states(
        self, date: TradingDate, symbols: Sequence[str] | None = None,
        *, with_touch_flags: bool = True,
    ) -> Mapping[str, object]:
        return self._require_state().states(
            date, symbols, with_touch_flags=with_touch_flags)

    # ── 公司行动（PoC-3 任务 3.2：dividend 实施态 → CorporateAction）──
    def corporate_actions(
        self, start: TradingDate, end: TradingDate
    ) -> list[CorporateAction]:
        """区间全市场实施态行动（升序 ex_date；引擎步骤①装载源）。

        合成规则（05 §9.4 T-06 → 04 §8.2.4）：
            stk_div_per_share = (stk_bo_rate or 0) + (stk_co_rate or 0)
            cash_div_per_share = cash_div or 0（None 行按 0——送转单独实施）
        仅 div_proc='实施'；ex_date 缺失行剔除（无除权时点不可分发）；
        pay_date 缺失保留 None（引擎 E5 回退：ex_date 当日到账）。

        同行动重复公告去重（PoC-3 实测：59,510 实施行中 1,228 条为
        同一行动多条实施行——仅 ann_date/imp_ann_date 等元数据字段
        不同，如 000001.SZ 20081031 两条 ann 0926/1016 全同参数；
        另有 cash_div 0.0/None 表现差异的重复公告如 600811 19960730
        行2/行3）。去重键 = 业务字段归一化（None≡0）后
        (symbol, ex_date, pay_date, record_date, bo, co, cd, end_date)
        ——end_date 入键保真正同日多年度行动（600811 19960730 的
        1994/1995 年度各一次送转）不被误删；保留首行。
        重复不除 → 引擎双重送转（000001 G9 事件链偏高 29% 实测）。

        读取路径（**IO-3/P1-8 区间化**，19 号附录 D.3）：`dividend` 年文件按
        **方案所属年度**归档——ex_date 可晚于文件年（2007 年度分红 2008 年
        实施），故**不可**按 ex_date 年直接裁剪（PoC-3 实测：600276 漏
        20080411 一次 10 送 2 派 0.7 → G9 恒等式偏差 17%）。
        今按「**ex_date 年 − 回看窗口**」读超集年份文件 + 行内 ex_date 区间
        过滤——正确性由超集性保证，代价从 37 文件降到 ~15（十年区间）。

        **回看窗口 = 5 年**（`_DIV_MAX_LAG_YEARS`）：实测全表 37 年
        「ex_date 年 − 文件年」分布 = {0:5448, 1:54083, 2:47, 3:3, 4:1, **5:2**}
        （最长 600708.SH 1997→20021128、000156.SZ 2007→20121019）。
        窗口**有守卫**：`tests/unit/test_dividend_lag_guard.py` 每次全表重测，
        一旦出现 > 5 年滞后即红——杜绝"未来数据静默漏行"。

        结果按 (root, start, end) **进程内 memo**（PF-9 同款有界缓存）：
        网格搜索逐组合调用同一区间，原每组合全表读 ≈0.7s。
        """
        memo_key = (str(self.root), start.to_ymd(), end.to_ymd())
        cached = _CORPORATE_ACTIONS_CACHE.get(memo_key)
        if cached is not None:
            return list(cached)

        y0, y1 = int(start.to_ymd()[:4]), int(end.to_ymd()[:4])
        years = list(range(y0 - _DIV_MAX_LAG_YEARS, y1 + 1))
        t = self._store.read_full(
            "dividend",
            columns=["ts_code", "ex_date", "pay_date", "record_date",
                     "ann_date", "div_proc", "stk_bo_rate", "stk_co_rate",
                     "cash_div", "end_date"],
            years=years,
        )
        import pyarrow.compute as pc

        t = t.filter(pc.and_(
            pc.and_(pc.equal(t.column("div_proc"), "实施"),
                    pc.greater_equal(t.column("ex_date"), start.to_ymd())),
            pc.less_equal(t.column("ex_date"), end.to_ymd()),
        ))
        seen: set[tuple] = set()
        out: list[CorporateAction] = []
        for r in t.to_pylist():
            if not r["ex_date"]:
                continue
            key = (r["ts_code"], r["ex_date"], r["pay_date"], r["record_date"],
                   float(r["stk_bo_rate"] or 0.0),
                   float(r["stk_co_rate"] or 0.0),
                   float(r["cash_div"] or 0.0), r["end_date"])
            if key in seen:
                continue
            seen.add(key)
            out.append(CorporateAction(
                symbol=r["ts_code"],
                ex_date=TradingDate.from_ymd(r["ex_date"]),
                pay_date=(TradingDate.from_ymd(r["pay_date"])
                          if r["pay_date"] else None),
                record_date=(TradingDate.from_ymd(r["record_date"])
                             if r["record_date"] else None),
                cash_div_per_share=float(r["cash_div"] or 0.0),
                stk_div_per_share=(float(r["stk_bo_rate"] or 0.0)
                                   + float(r["stk_co_rate"] or 0.0)),
                ann_date=(TradingDate.from_ymd(r["ann_date"])
                          if r["ann_date"] else None),
            ))
        out.sort(key=lambda a: (a.ex_date, a.symbol))
        _CORPORATE_ACTIONS_CACHE[memo_key] = tuple(out)     # IO-3 进程内 memo
        return out


# ─────────────────────────────────────────────────────────────
# 混合表日线 Feed（V3-1；M3 任务 6.3 引擎级净值复跑的前置）
# ─────────────────────────────────────────────────────────────
class FeedError(Exception):
    """Feed 使用纪律违例（如混合表请求全市场截面）。"""


class _MergedCrossSection(Mapping):
    """多表当日截面合并视图（V3-1）：逐表懒切片委托，先命中者返回。

    同一标的经路由只应出现在一个表中；取不到 → 截面缺席（撮合语义
    = 停牌/无行情，与单表原型一致）。各表切片仍为 `_LazyCrossSection`
    （按需构造 Bar，不整截面物化——B1/B2 性能纪律不破）。
    """

    __slots__ = ("_sections",)

    def __init__(self, sections: Sequence[Mapping[str, Bar]]):
        self._sections = tuple(sections)

    def __getitem__(self, key: str) -> Bar:
        for section in self._sections:
            bar = section.get(key)
            if bar is not None:
                return bar
        raise KeyError(key)

    def get(self, key: str, default: Bar | None = None):
        for section in self._sections:
            bar = section.get(key)
            if bar is not None:
                return bar
        return default

    def keys(self):
        merged: dict[str, None] = {}
        for section in self._sections:
            for code in section:
                merged.setdefault(code, None)
        return list(merged)

    def __iter__(self):
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.keys())

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and any(key in s for s in self._sections)


class MixedDailyFeed:
    """混合表日线 Feed（V3-1；M3 6.3）：股票 + ETF/LOF + 指数三表并行。

    与 `TushareParquetFeed` 的差异（显式留痕）：
        - `bars_of` / `history`：按 `daily_table_of` 代码段**路由**到对应表；
        - `bars(symbols, …)`：**必须显式 symbols**（多表逐日归并无"全市场
          截面"语义）——engine 经 `cross_section` 传入交易宇宙（本类以
          `requires_explicit_symbols = True` 声明）；
        - `universe` / `corporate_actions` / `trading_states`：委托股票侧
          原型；ETF 限价走**注入的状态合成器**的 `etf_limit` 通道（V3-2）。
    """

    contract_version = CONTRACT_VERSION
    #: 截面迭代必须显式给出 symbols（engine 据此传入交易宇宙）
    requires_explicit_symbols = True

    #: 日线列（三表同构，05 §9.4 T-01 / T-12）
    COLUMNS = ("ts_code", "trade_date", "open", "high", "low",
               "close", "pre_close", "vol", "amount")

    def __init__(self, root: Path | None = None, *, state_provider: Any = None):
        self.root = Path(root) if root else MARKET_DATA_DIR
        # R4：状态面板经构造期注入透传给股票侧原型（本模块亦不 import state）
        self._delegate = TushareParquetFeed(self.root,
                                            state_provider=state_provider)
        self._state = state_provider          # 仅为 delegate 同源校验/披露
        from btf.data.core import YearTableStore

        self._store = YearTableStore(self.root)
        #: PF-9/P2-5：原无界 dict（键=预载过的标的）→ LRU 有界
        self._bars_cache: BoundedDict[
            str, tuple[TradingDate, TradingDate, list[Bar]]] = BoundedDict(
                maxsize=8_000, name="mixed_feed.bars")

    # ── 委托股票侧原型 ──
    def universe(self, date: TradingDate) -> list[Instrument]:
        return self._delegate.universe(date)

    def universe_span(self, start: TradingDate,
                      end: TradingDate) -> list[Instrument]:
        """期间宇宙并集（P0-NEW-4）——委托股票侧原型（同 `universe`）。

        P2-NEW-4（19 号 §22.3）：委托须 `getattr` 兜底——delegate 若为自定义
        股票 Feed（未实现 `universe_span`），原直接调用即 `AttributeError`
        （runtime 侧的 getattr 保护反而掩盖了本层缺保护）。
        """
        span = getattr(self._delegate, "universe_span", None)
        if callable(span):
            return span(start, end)
        pool = {i.symbol: i for i in self._delegate.universe(end)}
        for inst in self._delegate.universe(start):
            pool.setdefault(inst.symbol, inst)
        return sorted(pool.values(), key=lambda i: i.symbol)

    def attach_state_provider(self, provider: Any) -> None:
        """注入状态面板合成器（装配根调用；透传给股票侧原型）。"""
        self._state = provider
        self._delegate.attach_state_provider(provider)

    @property
    def states(self):
        """状态面板合成器（`degraded_notes()` 等披露信息来源）。"""
        return self._delegate.states

    def preload(self, symbols: Sequence[str],
                start: TradingDate, end: TradingDate) -> int:
        """批量预载（V3-1 性能）：每表**一次**区间读 → 逐标的缓存。

        逐标的 `bars_of` 会按年重复读文件（88 标的 × 8 年 ≈700 次读）；
        整段回测（如奇点复跑）先经本方法预热——每表仅 3 次读。返回标的数。
        """
        groups: dict[str, list[str]] = {}
        for sym in dict.fromkeys(symbols):        # 去重且保序
            groups.setdefault(daily_table_of(sym), []).append(sym)
        loaded = 0
        for table, syms in sorted(groups.items()):
            t = self._store.read_codes(table, syms, start.to_ymd(), end.to_ymd(),
                           columns=list(self.COLUMNS))
            t = t.sort_by([("ts_code", "ascending"), ("trade_date", "ascending")])
            cols = {n: t.column(n).to_pylist() for n in t.column_names}
            grouped: dict[str, list[Bar]] = {}
            for i in range(t.num_rows):
                bar = _to_bar({n: cols[n][i] for n in t.column_names},
                              self._delegate.conversions)
                if bar is not None:
                    grouped.setdefault(bar.symbol, []).append(bar)
            for sym, bars in grouped.items():
                self._bars_cache[sym] = (start, end, bars)
                loaded += 1
        return loaded

    # ── 单标的时序（按表路由 + 预载缓存）──
    def bars_of(
        self, symbol: str, start: TradingDate, end: TradingDate, *,
        daily_table: str | None = None,
    ) -> list[Bar]:
        cached = self._bars_cache.get(symbol)
        if cached is not None and start >= cached[0] and end <= cached[1]:
            return [b for b in cached[2] if start <= b.date <= end]
        return self._delegate.bars_of(
            symbol, start, end,
            daily_table=daily_table or daily_table_of(symbol))

    def trading_states(
        self, date: TradingDate, symbols: Sequence[str] | None = None,
        *, with_touch_flags: bool = True,
    ) -> Mapping[str, object]:
        return self._delegate.trading_states(
            date, symbols, with_touch_flags=with_touch_flags)

    def corporate_actions(self, start: TradingDate, end: TradingDate):
        return self._delegate.corporate_actions(start, end)

    def history(self, symbol: str, end: TradingDate,
                fields: Sequence[str] | None = None, n_bars: int = 1) -> list[Bar]:
        bars = self.bars_of(symbol, TradingDate.from_ymd("19901219"), end)
        return bars[-n_bars:] if n_bars > 0 else []

    # ── 截面迭代（多表逐日归并；须显式 symbols）──
    def bars(
        self, symbols: Sequence[str] | None,
        start: TradingDate, end: TradingDate,
        *, daily_table: str | None = None,
    ) -> Iterator[tuple[TradingDate, Mapping[str, Bar]]]:
        if symbols is None:
            raise FeedError(
                "混合表 Feed 不支持全市场截面（须显式 symbols）——engine 请经 "
                "cross_section 传入交易宇宙；全市场口径请用 TushareParquetFeed")
        if daily_table is not None:
            raise FeedError("混合表 Feed 由代码段自动路由，不接受 daily_table 覆盖")
        wanted: dict[str, set[str]] = {"daily": set()}
        for sym in symbols:
            wanted.setdefault(daily_table_of(sym), set()).add(sym)
        iterators = {
            table: self._store.iter_day_slices(
                table, start.to_ymd(), end.to_ymd(),
                columns=list(self.COLUMNS))
            for table in sorted(wanted)
        }
        heads = {table: next(iterator, None)
                 for table, iterator in iterators.items()}
        while any(head is not None for head in heads.values()):
            current = min(head[0] for head in heads.values() if head is not None)
            sections: list[Mapping[str, Bar]] = []
            for table in sorted(heads):
                head = heads[table]
                if head is None or head[0] != current:
                    continue
                _ymd, cols, lo, hi = head
                sections.append(_LazyCrossSection(
                    cols, lo, hi, self._delegate.conversions, wanted[table]))
                heads[table] = next(iterators[table], None)
            yield TradingDate.from_ymd(current), _MergedCrossSection(sections)
