# -*- coding: utf-8 -*-
"""数据不变量校验（**批 8：CC-1..CC-6**；19 号 §39.2 裁决六-1）。

现状（19 号 §37.6 发现-4）：`feed.py` 只判 `None` 缺失——**无任何内部一致性
检查**；"价格尖刺 / 复权错位 / 日历错配"只能靠人对账。

六项校验（§39.2 表；口径经**真实主库实测**校正，见各函数 docstring）：
    CC-1 OHLC 内部一致性   high ≥ max(open,close) / low ≤ min(open,close) /
                          high ≥ low / close > 0 / vol ≥ 0 / amount ≥ 0
    CC-2 涨跌幅边界        单日涨跌幅超板块上限（主板 10%／创业·科创 20%／
                          北交所 30%（含 920xxx）／ST 5%）
    CC-3 日历一致性        ① 行情日期 ⊆ 交易日历（trade_cal is_open=1）；
                          ② **全日停牌日不得有行情行**（实测：2024 全市场
                          「全日停牌 ∩ daily = 0」——全日停牌不产行）
    CC-4 复权因子一致性    因子 > 0；除权事件 (symbol, ex_date) 有因子行；
                          个股因子序列**单调不减**（复权链断裂）
    CC-5 量价互斥          ① 有行情行则 vol>0 且 amount>0（实测：2024 全市场
                          「非停牌日 vol=0」= **0 行**）；② 收盘价越出当日涨跌停价

三条设计要点（§39.2 预付的坑；落地口径）：
    ① **fail-closed 边界**：本模块只**产出报告**；由 `runtime` 决定
       "装配期硬失败（`data.quality.strict=true`）或告警 + 记入产物 + 披露"。
       校验**自身**失败（缺日历/缺表）永远**硬失败**——拒绝静默跳过（新 16）。
    ② **区间化 + 谓词下推**（承 P1-8/IO-3 方向）：只读区间年文件与运行宇宙，
       走 `data.core.YearTableStore`（EX-4：唯一数据入口）；全部 Arrow 向量化
       （CC-4 单调性例外用 numpy，仍无 Python 逐行）。
    ③ **铁律新 15 / 新 16**：每条规则都有**能触发它的 fixture**
       （`tests/unit/test_data_quality.py`：12 例，逐条"故意破坏 → 必须变红"）；
       输出**不得恒空**——`checked_rows` 逐规则记录，`bt check` 有**负向对照**
       （破坏数据 ⇒ 机检必须失败）。

产物（CC-6）：`data_quality_report.json`（run 目录）+ manifest 摘要 + 报告
「数据质量」章节 + `bt check` 第 **8/8** 项常跑机检。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

__all__ = [
    "CHECK_IDS",
    "Finding",
    "QualityReport",
    "RuleResult",
    "run_checks",
]

#: 校验项编号（顺序即报告顺序）
CHECK_IDS = ("CC-1", "CC-2", "CC-3", "CC-4", "CC-5")

_RULE_NAMES = {
    "CC-1": "OHLC 内部一致性",
    "CC-2": "涨跌幅边界",
    "CC-3": "日历一致性",
    "CC-4": "复权因子一致性",
    "CC-5": "量价互斥",
}

#: 涨跌幅/限价容差（比例）：真实数据的四舍五入与复权舍入会产生 0.0x% 越界
_PCT_TOL = 0.005

#: **ST 股的涨跌幅上限**（批 9 接线后实测校正）：只有**主板** ST 收窄到 5%；
#: 创业板/科创板（300/301/688/689）与北交所（4xx/8xx/920）的 ST 股维持板块
#: 幅度（20% / 30%）——按"ST ⇒ 一律 5%"会在非主板产生大量假阳性。
_ST_MAIN_LIMIT = 0.05
#: **主板风险警示股票（ST/*ST）5% → 10%（与普通股一致）**：**2026-07-06 起施行**
#: （沪深《交易规则（2026 年修订）》2026-04-24 发布、2026-07-06 实施；
#: 科创板/创业板等其余板块**维持不变**）。EE-2 口径标定（OBS-3）。
_ST_MAIN_LIMIT_FROM = "20260706"
_MAIN_BOARD_LIMIT = 0.10          # 与 `_BOARD_LIMIT["main"]` 一致（见 `_board_limit`）
#: 价格最小变动单位（元）：**涨停价 = 前收 × (1±幅度) 四舍五入到分** ⇒ 低价股的
#: 实际可实现涨跌幅可略超名义上限（如 1.80 元股：1 档 = 0.56%）——容差须含 1 档。
_TICK = 0.01
#: **浮点松弛**（P3-NEW-6；19 号 §54.2.4）：档位容差的**可达上界**恰为
#: `Limit + _TICK/pre_close`（可达涨幅 = k·_TICK/pre，最大 k 使该式 ≤ Limit+1 档）
#: ⇒ 「恰在上界」的样本与容差**数学相等**，若不放 eps，判定结果由 **1 ULP** 决定
#: （`0.05555555555555558 > 0.05555555555555555` 会误报）。加法侧留松弛使边界
#: **确定性地判为"未越界"**，从而该分支可被单测**可区分地**证伪（否则只能证明方向）。
_PCT_EPS = 1e-9
#: **退市整理期**窗口（交易日）+ **首日不设涨跌幅**（EE-2 口径标定）：
#: ① 进入退市整理期**首日不设价格涨跌幅**（沪深《交易规则》/交易所投教问答）；
#: ② 整理期时长 **15 个交易日**（2020-12-31 退市新规，由 30 → 15）；
#: ③ 交易类退市**取消整理期**（无 `delist_date` 不入窗口）。
_DELIST_WINDOW_DAYS_OLD = 30
_DELIST_WINDOW_DAYS = 15
_DELIST_WINDOW_FROM = "20210101"
#: **恢复上市 / 重新上市首日**（暂停上市后恢复、退市后重新上市）**不设涨跌幅**
#: （深交所《交易规则》3.3.17 / 上交所同口径：「暂停上市后恢复上市的股票，
#: 恢复上市首日不实行价格涨跌幅限制」；2020 退市新规取消暂停/恢复上市环节）。
#: **近似口径登记**：主库无"暂停上市名单"，故以"**长期无行情缺口**"识别，
#: 阈值取 **250 个交易日（≈1 年）**——暂停上市通常 ≥1 年，而重组停牌极少超过，
#: 取大值以**避免**把"长期停牌重组复牌"（首日照常受限）误免。
_RESUME_GAP_DAYS = 250
#: 新股上市窗口（交易日）：注册制（创业/科创/北交所）上市**前 5 个交易日无涨跌幅**
#: ⇒ 以锚点起 7 个交易日窗口豁免（含首日，留 2 日冗余）
_IPO_WINDOW_DAYS = 7
#: **北交所开市日**：`*.BJ` 标的在**此前**属新三板序列（基础层集合竞价 ±50% /
#: 做市与协议转让无涨跌幅；精选层 2020-07-27 起 30%，但主库**无层级字段**）⇒
#: 交易机制无法从日线判定 ⇒ **明示豁免（口径外）**并披露计数（不静默放过）。
#: 2021-11-15 北交所开市起为 **30%**（首日不设）⇒ 之后按 30% 正常校验。
#: ⚠ **降级说明**：本豁免**覆盖了精选层时期（2020-07-27~2021-11-14）的 30%
#: 校验**——该时期真实越界将**不被检出**（宁可豁免、不误报）；待主库补层级字段
#: 后再收窄。
_BJ_BSE_FROM = "20211115"

#: **北交所开市首月**（2021-11-15 ~ 2021-12-31）**口径外窗口**：2021-11-15 开市首日
#: 不设涨跌幅；且首批平移股票的 `pre_close` 接续异常（NEEQ 长期停牌未入 `suspend_d`
#: ⇒ 复牌缺口无记录）——实测 219 行残余**全部集中于此窗口**。
#: ⇒ **明示豁免（口径外）+ 计数披露**；⚠ **降级说明**：该窗口内真实越界**不被检出**，
#: 待数据源侧核实 `pre_close` 连续性 / 补层级字段后收窄（EE-2 登记）。
_BSE_OPEN_FROM, _BSE_OPEN_TO = "20211115", "20211231"


#: dividend 年文件**方案年度**回看窗口（与 `data.feed._DIV_MAX_LAG_YEARS`
#: 同口径，实测最长 ex_date 滞后 5 年）——本模块不 import feed（铁律新 8）
_DIV_LOOKBACK_YEARS = 5

#: 板块涨跌幅上限（比例）；ST 单独按 0.05
_BOARD_LIMIT = {
    "main": 0.10,        # 主板（含中小板）
    "gem": 0.20,         # 创业板 300xxx
    "star": 0.20,        # 科创板 688xxx
    "bse": 0.30,         # 北交所 4xx/8xx/920xxx
}


def _board_limit(symbol: str) -> float:
    code = symbol.split(".", 1)[0]
    # 科创板 688/689；创业板 300/301/302（**301/302 曾漏判** → 3312 条假阳性）
    if code.startswith(("688", "689")):
        return _BOARD_LIMIT["star"]
    if code.startswith(("300", "301", "302")):
        return _BOARD_LIMIT["gem"]
    # 北交所：4xxxxx/8xxxxx/920xxx（920 于 2024 启用——首轮实测 6842 条假阳性
    # 即 920xxx 被误判主板 10% 所致）
    if code.startswith(("4", "8", "920")):
        return _BOARD_LIMIT["bse"]
    return _BOARD_LIMIT["main"]


@dataclass(frozen=True)
class Finding:
    """单条发现（含 ≤3 条样例，供人工复核）。"""

    rule: str
    table: str
    detail: str
    n_rows: int
    samples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "table": self.table, "detail": self.detail,
                "n_rows": self.n_rows, "samples": list(self.samples)}


@dataclass(frozen=True)
class RuleResult:
    rule: str
    checked_rows: int
    findings: tuple[Finding, ...] = ()

    @property
    def name(self) -> str:
        return _RULE_NAMES.get(self.rule, self.rule)

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def n_findings(self) -> int:
        return sum(f.n_rows for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "name": self.name, "ok": self.ok,
                "checked_rows": self.checked_rows,
                "n_findings": self.n_findings,
                "findings": [f.as_dict() for f in self.findings]}


@dataclass(frozen=True)
class QualityReport:
    """校验总报告（CC-6：可落盘、可披露、可机检）。"""

    start: str
    end: str
    symbols: int
    rules: tuple[RuleResult, ...]
    seconds: float = 0.0
    tables: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.rules)

    @property
    def n_findings(self) -> int:
        return sum(r.n_findings for r in self.rules)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": "dataquality.v1",
            "ok": self.ok,
            "period": [self.start, self.end],
            "symbols": self.symbols,
            "tables": list(self.tables),
            "seconds": round(self.seconds, 4),
            "n_findings": self.n_findings,
            "rules": [r.as_dict() for r in self.rules],
            **({"extra": self.extra} if self.extra else {}),
        }

    def summary_line(self) -> str:
        """单行摘要（日志/披露用；**不得恒空**——铁律新 16）。"""
        states = "、".join(
            f"{r.rule} {'PASS' if r.ok else f'FAIL({r.n_findings})'}"
            for r in self.rules)
        return (f"数据不变量校验：{'PASS' if self.ok else 'FAIL'}"
                f"（{self.start}–{self.end}，{self.symbols} 标的，"
                f"{self.seconds:.2f}s）| {states}")


# ─────────────────────────────────────────────────────────────
# 读取层（唯一 IO 入口 = data.core.YearTableStore；EX-4 / 铁律新 9 方向）
# ─────────────────────────────────────────────────────────────
def _years_between(start_ymd: str, end_ymd: str) -> list[int]:
    return list(range(int(start_ymd[:4]), int(end_ymd[:4]) + 1))


def _table_slice(store, table: str, start: str, end: str,
                 columns: tuple[str, ...], symbols: set[str] | None) -> Any:
    """区间表切片（**区间年文件 + 标的谓词下推 + 行内日期过滤**）。

    性能纪律（设计要点 ②）：`symbols` 非空时走 `read_year_where`（parquet
    `filters=` 行组裁剪）——20 标的十年实测 45.6s → 亚秒级；全市场口径
    （symbols=None）本就须读全表，如实记录耗时并披露。
    """
    parts = []
    if symbols is not None and not symbols:
        return pa.table({})            # 空标集：无行可校验（避免空 value_set 类型错）
    for year in _years_between(start, end):
        if symbols is not None:
            t = store.read_year_where(table, year, columns, set(symbols))
        else:
            t = store.load(table, year, columns, sorted_rows=True)
        if t is None or not t.num_rows:
            continue
        mask = pc.and_(pc.greater_equal(t.column("trade_date"), start),
                       pc.less_equal(t.column("trade_date"), end))
        t = t.filter(mask)
        if t.num_rows:
            parts.append(t)
    if not parts:
        return pa.table({})
    return pa.concat_tables(parts)


def _price_slice(store, table: str, start: str, end: str,
                 symbols: set[str] | None) -> Any:
    """区间价格表（CC-1..CC-5 的输入）。"""
    return _table_slice(store, table, start, end,
                        ("ts_code", "trade_date", "open", "high", "low",
                         "close", "pre_close", "vol", "amount"), symbols)


def _count(mask: Any) -> int:
    return int(pc.sum(pc.cast(mask, pa.int64())).as_py() or 0)


def _sample_cols(t: Any, mask: Any, n: int = 3) -> tuple[str, ...]:
    """取 ≤n 条违规样例（`ts_code@trade_date`；确定性取前 n）。"""
    hit = t.filter(mask)
    if not hit.num_rows:
        return ()
    codes = hit.column("ts_code").to_pylist()[:n]
    dates = hit.column("trade_date").to_pylist()[:n]
    return tuple(f"{c}@{d}" for c, d in zip(codes, dates, strict=False))


# ─────────────────────────────────────────────────────────────
# CC-1 / CC-2：单表内一致性（向量化）
# ─────────────────────────────────────────────────────────────
def _check_cc1(t: Any, table: str) -> RuleResult:
    if not t.num_rows:
        return RuleResult("CC-1", 0)
    hi, lo = t.column("high"), t.column("low")
    op, cl = t.column("open"), t.column("close")
    bad = pc.or_kleene(
        pc.or_kleene(pc.less(hi, pc.max_element_wise(op, cl)),
                     pc.greater(lo, pc.min_element_wise(op, cl))),
        pc.or_kleene(pc.less(hi, lo),
                     pc.or_kleene(pc.less_equal(cl, pa.scalar(0.0)),
                                  pc.or_kleene(
                                      pc.less(t.column("vol"), pa.scalar(0.0)),
                                      pc.less(t.column("amount"),
                                              pa.scalar(0.0))))))
    bad = pc.fill_null(bad, False)
    n = _count(bad)
    findings = ()
    if n:
        findings = (Finding("CC-1", table,
                            "OHLC/量额自相矛盾（high≥max(o,c) / low≤min(o,c) / "
                            "high≥low / close>0 / vol,amount≥0 之一被违反）",
                            n, _sample_cols(t, bad)),)
    return RuleResult("CC-1", t.num_rows, findings)


def _st_limit(code: str, day: str = "") -> float:
    """ST 股**当日**涨跌幅上限（**时点化**）：主板 ≤5%（2026-07-06 前）／=10%（其后）。

    依据（可复核，EE-2 口径标定）：
      ① 主板 ST/*ST 历史口径 5%（沪深《股票上市规则》）；
      ② **2026-07-06 起** 沪深主板风险警示股票涨跌幅由 5% 调整为 **10%**
         （与普通股一致；《交易规则（2026 年修订）》2026-04-24 发布、2026-07-06 实施）；
      ③ 创业板/科创板（20%）与北交所（30%）ST 股幅度**维持不变**
         （批 9 接线后实测反证：一律 5% 在非主板产生大量假阳性）。
    """
    board = _board_limit(code)
    if board != _MAIN_BOARD_LIMIT:
        return board
    return _ST_MAIN_LIMIT if (day or "99999999") < _ST_MAIN_LIMIT_FROM else board


def _delisting_windows(store, start: str, end: str, symbols: set[str] | None,
                       cal: list[str]) -> tuple[set[tuple[str, str]],
                                                set[tuple[str, str]]]:
    """退市整理期（`delist_date` 前 N 个交易日）→ (窗口内集合, **首日**集合)。

    口径（引自交易所规则/投教，见模块常量注释）：窗口 = 摘牌日前 **15** 个交易日
    （`delist_date ≥ 2021-01-01`；此前 **30**）；**首日**（窗口内最早交易日）
    **不设涨跌幅** → 单独返回以便豁免。`delist_date` 缺失或不在日历内 → 不入窗口。
    """
    out: set[tuple[str, str]] = set()
    first_days: set[tuple[str, str]] = set()
    basic = store.read_file("stock_basic", columns=["ts_code", "delist_date"])
    if basic is None:
        return out, first_days
    cal_arr = sorted(cal)
    index = {d: i for i, d in enumerate(cal_arr)}
    for code, dl in zip(basic.column("ts_code").to_pylist(),
                        basic.column("delist_date").to_pylist(), strict=True):
        if not dl:
            continue                                    # 交易类退市/在市 → 无整理期
        if symbols is not None and code not in symbols:
            continue
        ymd = str(dl)
        if not (start <= ymd <= end):
            continue
        pos = index.get(ymd)
        if pos is None:                                 # 摘牌日不在日历 → 定位其后
            pos = int(np.searchsorted(np.asarray(cal_arr), ymd))
        days = _DELIST_WINDOW_DAYS if ymd >= _DELIST_WINDOW_FROM \
            else _DELIST_WINDOW_DAYS_OLD
        window = cal_arr[max(0, pos - days):pos]
        for d in window:
            if start <= d <= end:
                out.add((code, d))
        if window:
            first_days.add((code, window[0]))
    return out, first_days


def _check_cc2(t: Any, table: str, st_pairs: set[tuple[str, str]],
               list_dates: dict[str, str], cal: set[str],
               suspend_full: set[tuple[str, str]] | None = None,
               delist_win: set[tuple[str, str]] | None = None,
               delist_first: set[tuple[str, str]] | None = None,
               ) -> tuple[RuleResult, dict[str, int]]:
    """涨跌幅边界（板块 + ST〔**时点化**〕 + 新股 + 复牌首日 + **退市整理期** + **价格档位容差**）。

    返回 `(结果, 豁免/口径计数 dict)`。豁免与口径（**全部来自实测归因 + 规则原文**）：
      · **新股**（首轮 3518 条假阳性，样例 301566.SZ@20240103）：注册制
        （创业/科创）上市**前 5 个交易日无涨跌幅限制** → 以 `list_date` 起
        **7 个交易日**窗口豁免（交易日口径：日历内计数，节假日不错位）。
      · **复牌首日**（次轮 34 条，样例 002089.SZ@20240326）：长期停牌复牌首日
        不适用常规限制 → 前一交易日为**全日停牌**者豁免。
      · **退市整理期（EE-2 / OBS-3）**：窗口内上限 = **板块幅度**（不执行 ST 5%）；
        **首日不设涨跌幅** → 首日豁免（《交易规则》4.5.6 等；窗口 15/30 交易日见
        `_delisting_windows`）。
      · **ST 时点化（EE-2 / OBS-3）**：主板 ST 5% 仅适用于 **2026-07-06 之前**；
        其后与普通股一致（10%）——见 `_st_limit`。
      · **价格档位容差（EE-2 / OBS-3）**：涨停价按**四舍五入到分**计算 ⇒ 容差取
        `max(_PCT_TOL, _TICK / pre_close)`（低价股 1 档可达 0.5%+，样例
        000005.SZ@20240222：5.56% vs 名义 5%）。
    """
    if not t.num_rows:
        return RuleResult("CC-2", 0), {}
    pre = t.column("pre_close")
    valid = pc.and_(pc.is_valid(pre), pc.greater(pre, pa.scalar(0.0)))
    ratio = pc.divide(t.column("close"),
                      pc.if_else(valid, pre, pa.scalar(1.0)))
    pct = pc.abs(pc.subtract(ratio, pa.scalar(1.0)))
    codes = t.column("ts_code").to_pylist()
    dates = t.column("trade_date").to_pylist()
    cal_sorted = sorted(cal)
    cal_arr = np.asarray(cal_sorted)
    day_pos = np.searchsorted(cal_arr, np.asarray(dates))
    susp = suspend_full or set()
    win = delist_win or set()
    first = delist_first or set()
    # 恢复/重新上市首日（近似口径）：长期缺口（≥ `_RESUME_GAP_DAYS`）后的首个行情日
    by_code: dict[str, list[int]] = {}
    for i, code in enumerate(codes):
        by_code.setdefault(code, []).append(int(day_pos[i]))
    long_gap_first: set[str] = set()
    for code, positions in by_code.items():
        positions.sort()
        for prev_pos, cur_pos in itertools.pairwise(positions):
            if cur_pos - prev_pos >= _RESUME_GAP_DAYS:
                long_gap_first.add(f"{code}@{cal_sorted[cur_pos]}")
    # 上市锚点 fallback：`list_date` 缺失（新上市未入 metadata 的代码）→ **首个行情日**
    first_pos_by_code: dict[str, int] = {}
    pos_set_by_code: dict[str, set[int]] = {}
    for code, pos in zip(codes, day_pos, strict=True):
        cur = int(pos)
        if code not in first_pos_by_code or cur < first_pos_by_code[code]:
            first_pos_by_code[code] = cur
        pos_set_by_code.setdefault(code, set()).add(cur)
    limits = []
    fresh_ipo = []
    ipo_anchor_fallback = []
    resumed = []
    in_delist = []
    exempt_delist_first = []
    exempt_long_gap = []
    exempt_neeq = []
    exempt_no_prev = []
    tick_relaxed = []
    for i, (code, day) in enumerate(zip(codes, dates, strict=True)):
        exempt_long_gap.append(f"{code}@{day}" in long_gap_first)
        # 新三板时期（精选层设立前）：机制多样 ⇒ 口径外豁免（计数披露）
        # `*.BJ` 段**整体口径外**（EE-2 四轮归因登记）：① 新三板机制多样
        # （基础层集合竞价 ±50% / 做市·协议转让无限制）；② 北交所开市首日不限；
        # ③ NEEQ 长期停牌致 `pre_close` 接续异常（开市首月尤为集中）；
        # ④ 2022 年初仍有 >30% 跳变（成因未定，疑数据侧）。
        # 实测残余 2005 → 824 → 219 → 155 → 64 行**全部**落在该段 ⇒ 主库缺
        # “层级 / 交易方式 / 停牌标记”字段，工具侧无法标定 ⇒ **明示豁免**（计数 +
        # 降级说明：该段真实越界不被检出；待数据源补字段后收窄）。
        exempt_neeq.append(code.endswith(".BJ"))
        # **原则性口径（EE-2）**：前一交易日**无行情** ⇒ 涨跌幅不可比 ⇒ 不判。
        # 覆盖：停牌/复牌（含 NEEQ 长期停牌无 `suspend_d` 记录）、新挂牌首日、
        # 数据缺口——比"依赖 suspend_d 记录"更稳健（数据自证）。
        exempt_no_prev.append(int(day_pos[i]) - 1
                              not in pos_set_by_code.get(code, ()))
        # 口径优先级：**退市整理期 > ST > 板块**
        #   · 整理期：涨跌幅 = 板块幅度（主板 10%；不再执行 ST 5%）；
        #   · ST：**时点化**（主板 2026-07-06 前 5%，其后 10%；非主板维持板块幅度）；
        #   · 其余：板块幅度。
        if (code, day) in win:
            lim = _board_limit(code)
            in_delist.append(True)
            exempt_delist_first.append((code, day) in first)
        else:
            lim = _st_limit(code, day) if (code, day) in st_pairs else \
                _board_limit(code)
            in_delist.append(False)
            exempt_delist_first.append(False)
        limits.append(lim)
        # 价格档位容差：1 档 / pre_close（涨停价四舍五入到分）
        prev = pre[i].as_py()
        tick_relaxed.append(bool(prev) and prev > 0
                            and (_TICK / float(prev)) > _PCT_TOL)
        listed = list_dates.get(code)
        # 新股豁免**只在上市日落在校验日历窗口内**时成立：上市日早于窗口
        # 起点者（searchsorted 恒 0）会把窗口前 8 个交易日误判为"上市初期"
        # （首版 fixture 实测：老股被整体豁免 → CC-2 漏检）。
        # EE-2 增补：`list_date` **缺失**（新上市未入 metadata，样例 688826.SH@20260818
        # 首日 +516%）→ 以**首个行情日**为锚（数据可核），窗口同 7 交易日。
        if listed:
            # 上市日**早于窗口起点** ⇒ 老股：**绝不豁免**（防整段被豁免）
            if listed >= cal_sorted[0]:
                list_pos = int(np.searchsorted(cal_arr, listed, side="left"))
                fresh_ipo.append(
                    0 <= int(day_pos[i]) - list_pos <= _IPO_WINDOW_DAYS)
            else:
                fresh_ipo.append(False)
            ipo_anchor_fallback.append(False)
        else:
            # `list_date` **缺失**（新上市未入 metadata）→ 首个行情日为锚
            anchor_pos = first_pos_by_code.get(code, -1)
            ok_ipo = (anchor_pos >= 0
                      and 0 <= int(day_pos[i]) - anchor_pos <= _IPO_WINDOW_DAYS)
            fresh_ipo.append(ok_ipo)
            ipo_anchor_fallback.append(ok_ipo)
        pos = int(day_pos[i])
        prev_day = cal_sorted[pos - 1] if pos > 0 else ""
        resumed.append(bool(prev_day) and (code, prev_day) in susp)
    exempt = pa.array([a or b or c or d or e or f for a, b, c, d, e, f in
                       zip(fresh_ipo, resumed, exempt_delist_first,
                           exempt_long_gap, exempt_neeq, exempt_no_prev,
                           strict=True)])
    checked = _count(pc.and_(valid, pc.invert(exempt)))
    # 有效容差 = max(_PCT_TOL, 1 档/pre_close)
    tick_tol = pc.divide(pa.scalar(_TICK),
                         pc.if_else(valid, pre, pa.scalar(1.0)))
    eff_tol = pc.max_element_wise(pa.scalar(_PCT_TOL), tick_tol)
    # 比较侧留浮点松弛（`_PCT_EPS`）：档位可达上界与容差数学相等 ⇒ 无 eps 时
    # 边界由 1 ULP 决定（P3-NEW-6）。语义：**恰在上界 ⇒ 未越界**（确定性）。
    bad = pc.and_(pc.and_(valid, pc.invert(exempt)),
                  pc.greater(pct, pc.add(pc.add(pa.array(limits), eff_tol),
                                         pa.scalar(_PCT_EPS))))
    bad = pc.fill_null(bad, False)
    n = _count(bad)
    findings = ()
    if n:
        findings = (Finding("CC-2", table,
                            "单日涨跌幅越板块/ST/整理期上限（价格尖刺或复权错位）",
                            n, _sample_cols(t, bad)),)
    return (RuleResult("CC-2", checked, findings),
            {"exempt_ipo_rows": sum(1 for x in fresh_ipo if x),
             "exempt_resumption_rows": sum(1 for x in resumed if x),
             "exempt_delisting_first_rows":
                 sum(1 for x in exempt_delist_first if x),
             "exempt_resume_listing_rows":
                 sum(1 for x in exempt_long_gap if x),
             "exempt_ipo_anchor_fallback_rows":
                 sum(1 for x in ipo_anchor_fallback if x),
             "exempt_neeq_period_rows": sum(1 for x in exempt_neeq if x),
             "exempt_missing_prev_rows":
                 sum(1 for x in exempt_no_prev if x),
             "delisting_window_rows": sum(1 for x in in_delist if x),
             "tick_tolerance_rows": sum(1 for x in tick_relaxed if x)})


def _list_dates(store) -> dict[str, str]:
    """stock_basic → {symbol: list_date}（CC-2 新股豁免用）。"""
    t = store.read_file("stock_basic", columns=["ts_code", "list_date"])
    if t is None:
        return {}
    return {c: d for c, d in zip(t.column("ts_code").to_pylist(),
                                 t.column("list_date").to_pylist(),
                                 strict=True) if c and d}


# ─────────────────────────────────────────────────────────────
# CC-3：日历一致性
# ─────────────────────────────────────────────────────────────
def calendar_days(root: Path | str, start: str, end: str) -> set[str]:
    """交易日历（公开）：`trade_cal` 中 `is_open=1` 的区间交易日集合。

    公开的目的（批 9 / DD-1）：CC-2 的 **ST 名单日期维度必须与校验日历同源**
    （§44.2 边界 1）——调用方（`runtime`）用本函数取 `cal` 后交给
    `StateSynthesizer.st_pairs(cal=…)`，与 `run_checks` 内部使用的是**同一口径**。
    """
    return _calendar_days(Path(root), start, end)


def _calendar_days(root: Path, start: str, end: str) -> set[str]:
    """交易日历（`trade_cal`，is_open=1；**布局由 `tables_meta` 单源决定**）。

    EX-4（19 号 §49）：读取经 `core`（原直呼 `pq.read_table`）。原实现还有一个
    非登记布局兜底（`root/trade_cal/trade_cal.parquet`）——**已移除**：布局唯一
    真源 = `tables_meta.METADATA`（`root/metadata/trade_cal.parquet`），且该兜底
    无测试/无部署依据；缺失时仍**fail-closed**（显式报错，不静默跳过）。
    """
    from btf.data.core import YearTableStore

    t = YearTableStore(root).read_file("trade_cal")
    if t is None:
        raise FileNotFoundError(
            f"交易日历缺失（trade_cal）——CC-3 无法进行，**拒绝静默跳过**："
            f"{root / 'metadata' / 'trade_cal.parquet'}（登记布局 = metadata）")
    if "is_open" not in t.column_names:
        raise ValueError("trade_cal 缺 is_open 列——CC-3 无法判定，拒绝静默跳过")
    t = t.filter(pc.equal(t.column("is_open"), 1))
    days = t.column("cal_date").to_pylist()
    return {str(d) for d in days if d is not None and start <= str(d) <= end}


def _check_cc3(t: Any, table: str, cal: set[str],
               suspend_full_day: set[tuple[str, str]]) -> RuleResult:
    """① 行情日期 ⊆ 交易日历；② **全日停牌日不得有行情行**。

    ②口径来自**实测**（2024 全市场：全日停牌 ∩ daily = **0**；盘中停牌
    41/41 有行且 vol>0）——全日停牌（`suspend_type=S` 且 `suspend_timing`
    为空）时行情表**不产出行**，故"有行"即状态/行情错配。
    （首轮误按"停牌必须有行"实现 → 20 标的十年报出 23 万条假阳性，已纠正。）
    """
    if not t.num_rows:
        return RuleResult("CC-3", 0)
    cal_arr = pa.array(sorted(cal))
    off_cal = pc.fill_null(
        pc.invert(pc.is_in(t.column("trade_date"), value_set=cal_arr)), False)
    findings: list[Finding] = []
    n_off = _count(off_cal)
    if n_off:
        findings.append(Finding(
            "CC-3", table, "行情日期不在交易日历内（trade_cal is_open=1）",
            n_off, _sample_cols(t, off_cal)))
    if suspend_full_day:
        hit = [(c, d) for c, d in zip(t.column("ts_code").to_pylist(),
                                      t.column("trade_date").to_pylist(),
                                      strict=True)
               if (c, d) in suspend_full_day]
        if hit:
            findings.append(Finding(
                "CC-3", table,
                "全日停牌日出现行情行（状态与行情错配；该口径下停牌日应无行）",
                len(hit), tuple(f"{c}@{d}" for c, d in hit[:3])))
    return RuleResult("CC-3", t.num_rows, tuple(findings))


# ─────────────────────────────────────────────────────────────
# CC-4：复权因子（>0 / 除权事件有因子行 / 个股因子序列单调不减）
# ─────────────────────────────────────────────────────────────
def _check_cc4(store, start: str, end: str,
               symbols: set[str] | None) -> RuleResult:
    """因子正性 + 个股序列单调不减（numpy 向量化；无 Python 逐行）。"""
    checked = 0
    bad_rows = 0
    bad_samples: list[str] = []
    nonmono = 0
    nonmono_samples: list[str] = []
    t = _table_slice(store, "adj_factor", start, end,
                     ("ts_code", "trade_date", "adj_factor"), symbols)
    if t.num_rows:
        t = t.sort_by([("ts_code", "ascending"), ("trade_date", "ascending")])
        checked = t.num_rows
        vals = np.asarray(t.column("adj_factor").to_numpy(zero_copy_only=False),
                          dtype="float64")
        bad = ~np.isfinite(vals) | (vals <= 0)
        bad_rows = int(bad.sum())
        if bad_rows:
            days = t.column("trade_date").to_pylist()
            syms = t.column("ts_code").to_pylist()
            bad_samples = [f"{syms[i]}@{days[i]} adj_factor={vals[i]}"
                           for i in np.flatnonzero(bad)[:3]]
        codes = t.column("ts_code").combine_chunks().dictionary_encode()
        idx = np.asarray(codes.indices.to_numpy(zero_copy_only=False))
        new_group = np.empty(len(idx), dtype=bool)
        new_group[0] = True
        new_group[1:] = idx[1:] != idx[:-1]
        delta = np.empty(len(vals), dtype="float64")
        delta[0] = 0.0
        delta[1:] = vals[1:] - vals[:-1]
        # 相对容差 1e-4（实测：adj_factor 以 4 位小数发布，"下降"多为末位
        # 舍入——如 125.0496→125.0493 = −0.00024%；真断链至少是量级下降）
        prev_vals = np.empty_like(vals)
        prev_vals[0] = vals[0]
        prev_vals[1:] = vals[:-1]
        # 容差 = max(1e-3 绝对, 1e-4 相对)：adj_factor 以 4 位小数发布，
        # 末位舍入会产生 2× 末位量级的小幅"下降"（实测 2.987→2.9867）；
        # 真断链是**量级**下降（如 2.99→1.50），1e-3 门限不会漏。
        tol = np.maximum(1e-3, 1e-4 * np.abs(prev_vals))
        dropped = (~new_group) & (delta < -tol)
        nonmono = int(dropped.sum())
        if nonmono:
            pos = np.flatnonzero(dropped)[:3]
            days = t.column("trade_date").to_pylist()
            syms = t.column("ts_code").to_pylist()
            nonmono_samples = [f"{syms[i]}@{days[i]} "
                               f"{vals[i - 1]}→{vals[i]}" for i in pos]
    findings: list[Finding] = []
    if bad_rows:
        findings.append(Finding("CC-4", "adj_factor", "复权因子非正/缺失",
                                bad_rows, tuple(bad_samples[:3])))
    if nonmono:
        findings.append(Finding("CC-4", "adj_factor",
                                "个股因子序列出现下降（复权链断裂嫌疑）",
                                nonmono, tuple(nonmono_samples)))
    return RuleResult("CC-4", checked, tuple(findings))


def _check_cc4_events(store, start: str, end: str,
                      symbols: set[str] | None) -> RuleResult:
    """除权事件须有对应因子行（(symbol, ex_date) 存在性）。

    事件源**本模块直读** `dividend`（不 import `data.feed`——避免新增
    data 域内耦合，铁律新 8 方向）；口径与引擎一致：`div_proc='实施'` ∧
    `ex_date ∈ [start, end]` ∧ ex_date 非空；年份按**方案年度**回看窗口
    （`_DIV_LOOKBACK_YEARS`，与 feed 同口径）。
    """
    parts = []
    for year in range(int(start[:4]) - _DIV_LOOKBACK_YEARS, int(end[:4]) + 1):
        t = store.read_file("dividend", year=year,
                            columns=["ts_code", "ex_date", "div_proc"])
        if t is None:
            continue
        if symbols is not None:
            t = t.filter(pc.is_in(t.column("ts_code"),
                                  value_set=pa.array(sorted(symbols)).cast(
                                      t.column("ts_code").type)))
        parts.append(t)
    if not parts:
        return RuleResult("CC-4", 0)
    t = pa.concat_tables(parts)
    t = t.filter(pc.and_(
        pc.and_(pc.equal(t.column("div_proc"), "实施"),
                pc.is_valid(t.column("ex_date"))),
        pc.and_(pc.greater_equal(t.column("ex_date"), start),
                pc.less_equal(t.column("ex_date"), end))))
    need = set(zip(t.column("ts_code").to_pylist(),
                   t.column("ex_date").to_pylist(), strict=True))
    if not need:
        return RuleResult("CC-4", 0)
    adj = _table_slice(store, "adj_factor", start, end,
                       ("ts_code", "trade_date"), symbols)
    have = set(zip(adj.column("ts_code").to_pylist(),
                   adj.column("trade_date").to_pylist(), strict=True))
    missing = sorted(need - have)
    if not missing:
        return RuleResult("CC-4", len(need))
    return RuleResult("CC-4", len(need), (Finding(
        "CC-4", "dividend→adj_factor",
        "除权事件在 adj_factor 无对应因子行（复权链缺口）",
        len(missing), tuple(f"{c}@{d}" for c, d in missing[:3])),))


# ─────────────────────────────────────────────────────────────
# CC-5：量价互斥（有价无量；收盘越出涨跌停价）
# ─────────────────────────────────────────────────────────────
def _check_cc5_zerovol(t: Any, table: str) -> RuleResult:
    """有行情行 ⇒ vol>0 且 amount>0（实测 2024 全市场「非停牌日 vol=0」= 0 行）。"""
    if not t.num_rows:
        return RuleResult("CC-5", 0)
    vol = t.column("vol")
    amt = t.column("amount")
    bad = pc.fill_null(pc.or_(
        pc.or_(pc.is_null(vol), pc.less_equal(vol, pa.scalar(0.0))),
        pc.or_(pc.is_null(amt), pc.less_equal(amt, pa.scalar(0.0)))), True)
    n = _count(bad)
    findings = ()
    if n:
        findings = (Finding("CC-5", table,
                            "有行情行但 vol/amount 为 0（有价无量——停牌口径冲突"
                            "或数据缺口标记）", n, _sample_cols(t, bad)),)
    return RuleResult("CC-5", t.num_rows, findings)


def _check_cc5_limit(store, table: str, limit_table: str, start: str, end: str,
                     symbols: set[str] | None) -> RuleResult:
    """收盘价须落在当日 [down_limit, up_limit] 内（Arrow join 向量化）。"""
    t = _table_slice(store, table, start, end,
                     ("ts_code", "trade_date", "close"), symbols)
    lt = _table_slice(store, limit_table, start, end,
                      ("ts_code", "trade_date", "up_limit", "down_limit"),
                      symbols)
    if not t.num_rows or not lt.num_rows:
        return RuleResult("CC-5", 0)
    keys = ["ts_code", "trade_date"]
    joined = t.join(lt, keys=keys, join_type="inner")
    if not joined.num_rows:
        return RuleResult("CC-5", 0)
    up, dn = joined.column("up_limit"), joined.column("down_limit")
    close = joined.column("close")
    ok_rows = pc.fill_null(pc.and_(
        pc.and_(pc.is_valid(up), pc.is_valid(dn)), pc.is_valid(close)), False)
    bad = pc.and_(ok_rows, pc.or_(
        pc.greater(close, pc.multiply(up, pa.scalar(1.0 + _PCT_TOL))),
        pc.less(close, pc.multiply(dn, pa.scalar(1.0 - _PCT_TOL)))))
    n = _count(bad)
    findings = ()
    if n:
        hit = joined.filter(bad)
        samples = tuple(
            f"{c}@{d} close={cl} 限价[{lo},{hi}]"
            for c, d, cl, lo, hi in zip(
                hit.column("ts_code").to_pylist()[:3],
                hit.column("trade_date").to_pylist()[:3],
                hit.column("close").to_pylist()[:3],
                hit.column("down_limit").to_pylist()[:3],
                hit.column("up_limit").to_pylist()[:3], strict=False))
        findings = (Finding("CC-5", limit_table,
                            "收盘价越出当日涨跌停价（限价与行情冲突/复权错位）",
                            n, samples),)
    return RuleResult("CC-5", _count(ok_rows), findings)


# ─────────────────────────────────────────────────────────────
# 总入口
# ─────────────────────────────────────────────────────────────
def _full_day_suspensions(store, start: str, end: str,
                          symbols: set[str] | None) -> set[tuple[str, str]]:
    """全日停牌对（`suspend_type=S` 且 `suspend_timing` 为空）。

    实测口径（2024）：S 全日 3368 / S 盘中 43 / R（复牌）321——**盘中停牌与
    复牌记录不构成"不可交易"**，只有 S+无 timing 才是全天停牌。
    """
    out: set[tuple[str, str]] = set()
    if symbols is not None and not symbols:
        return out
    for year in _years_between(start, end):
        t = store.read_file(
            "suspend_d", year=year,
            columns=["ts_code", "trade_date", "suspend_timing",
                     "suspend_type"])
        if t is None:
            continue
        mask = pc.and_(
            pc.greater_equal(t.column("trade_date"), start),
            pc.less_equal(t.column("trade_date"), end))
        t = t.filter(mask)
        if symbols is not None:
            t = t.filter(pc.is_in(
                t.column("ts_code"),
                value_set=pa.array(sorted(symbols)).cast(
                    t.column("ts_code").type)))
        for code, day, timing, typ in zip(t.column("ts_code").to_pylist(),
                                          t.column("trade_date").to_pylist(),
                                          t.column("suspend_timing").to_pylist(),
                                          t.column("suspend_type").to_pylist(),
                                          strict=True):
            if typ == "S" and not timing:
                out.add((code, day))
    return out


def run_checks(
    root: Path | str,
    start_ymd: str,
    end_ymd: str,
    *,
    symbols: set[str] | None = None,
    price_tables: tuple[str, ...] = ("daily",),
    limit_tables: tuple[str, ...] = ("stk_limit",),
    st_symbols: set[tuple[str, str]] | None = None,
) -> QualityReport:
    """区间 + 宇宙裁剪的六项校验（CC-1..CC-5；CC-6 由调用方落盘/披露）。

    `st_symbols`：ST `(symbol, date)` 集合（CC-2 的 **5% 上限**）。

    ⚠ **语义订正（DD-3 / P2-NEW-8，19 号 §43.3 / §44）**：原注释写"None →
    按板块上限（**偏保守**：ST 股只会被放宽，不会造成假阳性）"——**该措辞与
    事实相反**：`None` 时 ST 股按**板块上限**（主板 10%）判定 ⇒ ST 股
    5%~10% 的越界**会被漏检（false negative）**，是"放宽"而非"保守"。
    故：**调用方必须传入真实 ST 集合**（`StateSynthesizer.st_pairs`），
    否则 CC-2 的 ST 分支不生效（已由 `runtime._run_data_quality` 接线）。

    `price_tables`/`limit_tables`：股票 `daily`+`stk_limit`；ETF 场景可传
    `fund_daily`+`etf_limit`（成对，顺序对应）。
    """
    from time import perf_counter

    from btf.data.core import YearTableStore

    t0 = perf_counter()
    root = Path(root)
    store = YearTableStore(root)
    cal = _calendar_days(root, start_ymd, end_ymd)
    suspend_full = _full_day_suspensions(store, start_ymd, end_ymd, symbols)
    st_pairs = st_symbols or set()
    list_dates = _list_dates(store)
    # EE-2／OBS-3：退市整理期窗口（首日不设涨跌幅 → 单独豁免）
    delist_win, delist_first = _delisting_windows(store, start_ymd, end_ymd,
                                                 symbols, sorted(cal))

    rules: list[RuleResult] = []
    checked_symbols = 0
    tables_used: list[str] = []
    cc2_counts: dict[str, int] = {}
    for table in price_tables:
        t = _price_slice(store, table, start_ymd, end_ymd, symbols)
        if not t.num_rows:
            continue
        tables_used.append(table)
        checked_symbols = (len(symbols) if symbols is not None
                           else len(set(t.column("ts_code").to_pylist())))
        rules.append(_check_cc1(t, table))
        cc2, counts = _check_cc2(t, table, st_pairs, list_dates, cal,
                                 suspend_full, delist_win, delist_first)
        rules.append(cc2)
        for key, value in counts.items():
            cc2_counts[key] = cc2_counts.get(key, 0) + value
        rules.append(_check_cc3(t, table, cal, suspend_full))
        rules.append(_check_cc5_zerovol(t, table))
    for price_table, limit_table in zip(price_tables, limit_tables,
                                        strict=False):
        rules.append(_check_cc5_limit(store, price_table, limit_table,
                                      start_ymd, end_ymd, symbols))
    rules.append(_check_cc4(store, start_ymd, end_ymd, symbols))
    rules.append(_check_cc4_events(store, start_ymd, end_ymd, symbols))

    merged: dict[str, list[RuleResult]] = {r: [] for r in CHECK_IDS}
    for r in rules:
        merged.setdefault(r.rule, []).append(r)
    out = [
        RuleResult(rule,
                   sum(g.checked_rows for g in merged.get(rule, [])),
                   tuple(f for g in merged.get(rule, []) for f in g.findings))
        for rule in CHECK_IDS
    ]
    return QualityReport(
        start=start_ymd, end=end_ymd, symbols=checked_symbols,
        rules=tuple(out), seconds=perf_counter() - t0,
        tables=tuple(tables_used),
        extra={"calendar_days": len(cal),
               "full_day_suspensions": len(suspend_full),
               **cc2_counts,
               # 口径条款（EE-2：`st_source` 的等价口径标识；**可核、带生效日期**）
               "clauses": {
                   "st_main_limit": {
                       "value_before": _ST_MAIN_LIMIT,
                       "value_from": _MAIN_BOARD_LIMIT,
                       "effective_from": _ST_MAIN_LIMIT_FROM,
                       "note": "沪深主板风险警示股票涨跌幅 5% → 10%"
                               "（《交易规则（2026 年修订）》2026-07-06 施行）；"
                               "科创板/创业板等其余板块维持不变",
                   },
                   "delisting_period": {
                       "window_days": _DELIST_WINDOW_DAYS,
                       "window_days_before": _DELIST_WINDOW_DAYS_OLD,
                       "effective_from": _DELIST_WINDOW_FROM,
                       "first_day_unlimited": True,
                       "note": "退市整理期首日不设涨跌幅；期限 15 个交易日"
                               "（2020-12-31 退市新规由 30 缩短）；"
                               "交易类退市无整理期",
                       "rows_in_window": cc2_counts.get("delisting_window_rows", 0),
                       "rows_first_day_exempt":
                           cc2_counts.get("exempt_delisting_first_rows", 0),
                   },
                   "price_tick": {
                       "tick_yuan": _TICK,
                       "note": "涨停价 = 前收×(1±幅度) 四舍五入到分 ⇒ "
                               "容差含 1 档（低价股尤显）",
                       "rows_tick_relaxed":
                           cc2_counts.get("tick_tolerance_rows", 0),
                   },
                   "missing_prev_row": {
                       "note": "前一交易日无行情 ⇒ 涨跌幅不可比 ⇒ 不判"
                               "（覆盖停牌/复牌、新挂牌首日、数据缺口；"
                               "比依赖 suspend_d 记录更稳健）",
                       "rows_exempt":
                           cc2_counts.get("exempt_missing_prev_rows", 0),
                   },
                   "bse_segment": {
                       "scope": "*.BJ（北交所 / 新三板序列）全段",
                       "open_from": _BSE_OPEN_FROM,
                       "note": "四轮归因（残余 2005→824→219→155→64 行全部落此段）："
                               "新三板机制多样（±50%/做市无限制）+ 北交所开市首日"
                               "不限 + NEEQ 长期停牌致 pre_close 接续异常 + 2022 "
                               "年初>30% 跳变成因未定 ⇒ 主库缺“层级/交易方式/停牌"
                               "标记”字段 ⇒ **明示豁免**；**降级说明**：该段涨跌幅"
                               "真实性**不在 CC-2 覆盖内**，待数据源补字段后收窄",
                       "rows_exempt": cc2_counts.get("exempt_neeq_period_rows", 0),
                   },
                   "neeq_period": {
                       "until": _BJ_BSE_FROM,
                       "note": "新三板时期（`*.BJ` 且早于北交所开市 2021-11-15）：基础层集合"
                               "竞价 ±50% / 做市·协议转让无涨跌幅 ⇒ 日线无法"
                               "判定交易方式 ⇒ **明示豁免（口径外）**；降级说明："
                               "覆盖精选层 30% 校验（主库无层级字段）",
                       "rows_exempt": cc2_counts.get("exempt_neeq_period_rows", 0),
                   },
                   "ipo_anchor": {
                       "window_days": _IPO_WINDOW_DAYS,
                       "note": "新股豁免锚点：`list_date` 优先；**缺失时以首个行情日"
                               "为锚**（新上市未入 metadata 的代码，样例 688826.SH"
                               "@20260818 首日 +516%）",
                       "rows_anchor_fallback":
                           cc2_counts.get("exempt_ipo_anchor_fallback_rows", 0),
                   },
                   "resume_listing": {
                       "min_gap_days": _RESUME_GAP_DAYS,
                       "note": "暂停上市后恢复上市 / 退市后重新上市首日不设"
                               "涨跌幅（深《交易规则》3.3.17 等）；2020 退市新规"
                               "取消暂停/恢复上市环节。**近似口径**：以长期无行情"
                               "缺口识别（阈值 250 交易日，避免误免重组停牌复牌）",
                       "rows_exempt":
                           cc2_counts.get("exempt_resume_listing_rows", 0),
                   },
               }})
