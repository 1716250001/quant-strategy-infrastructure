# -*- coding: utf-8 -*-
"""ETF/LOF 公司行动（**CL-2-H**；ADR-20；19 号 §96.4 R32′ / R35′ / R34′）。

只服务 **ETF/LOF** 宇宙（股票侧走 `dividend` ＋ `corp_action_probe` 离线清单；
判定复用 `is_fund_code`，**禁第二套代码段判定**——ADR-20 ①）。

两个行动族：

1. **基金分红（R32′，源 `fund_div`，20 号表）**：
   - `ex_date` 份额不变（除息价由行情承担）；`pay_date` 现金到账 `qty × div_cash`；
   - **`div_cash` 即每份金额**——**禁止除以 `base_unit`**（ADR-20 第 3 条；实证锚：
     510880 11/11 笔与「前收 − pre_close」零误差）；
   - 按 `(ts_code, ex_date)` **去重**（510310 2024-10-21 两条同金额行，不去重双倍入账）；
   - 只消费 `div_proc='实施'`（全库存在「分红方案待定」）；
   - **回退链** `pay_date → earpay_date → ex_date`（**任何回退必须披露**）；
   - `pay_date` 非交易日 ⇒ **顺延至下一交易日**入账并披露。

2. **份额折算（R35′，双证据）**：
   - `R = pre_close(t) ÷ close(t−1)`（交易所除权价，权威）；
   - 与 `fund_adj` 因子跳变互校——**因子跳变 ＝ R 的倒数**（DOC-13/N37）：
     互校式 **`|R × (f_t ÷ f_{t−1}) − 1| < tolerance`**（缺省 0.5%）；
     **禁止直接比较 R 与因子比值**（将得 75%~800% 偏差 ⇒ 全盘 fail-closed）；
   - 不符 ⇒ **fail-closed**（**不建模** ＋ 披露，不得静默取其一）；
   - 入账经 `CorporateAction.consolidation_ratio = 1/R`（与股票缩股同构：
     `qty ×= ratio`、成本总额守恒）；
   - **禁用 `fund_share` 判定折算**（折算日份额不变；ADR-20 第 6 条）。

折算候选判据（19 号 §91.3-B，实测 8 只 9 事件全中）：
单日 `|close_t/close_{t−1} − 1| > 12%` **且** `|close_t/pre_close_t − 1| < 5%`
（跳变已被交易所除权价吸收 ⇒ 份额事件而非行情）。
**R43′（N46，2026-10-08）**：候选与 `fund_div` **除息日交叉**——同日存在除息记录
⇒ 判为**分红**、不计折算（分红与折算在双证据上数学同构，`|jump|>12%` 是唯一
防线且无语义依据；全库可比分红 >12% 共 218 笔、最高 70.47%）。

**R44′（N47，2026-10-08）**：`fund_div` **缺失/不可读** ⇒ 显式告警＋披露
（**禁静默**——对齐 `trade_cal` 的「拒绝静默跳过」）；表在而区间 0 条 ⇒ 亦披露。
**R45′（N49，2026-10-08）**：同日同标的**顺序＝先折算后分红**（ADR-20 第 ⑩ 条；
分红现金按折算后份额计）。
"""
from __future__ import annotations

import bisect
import logging
import pathlib
from collections.abc import Iterable, Sequence
from typing import Any

from btf.domain.action import CorporateAction, ratio_or_default
from btf.domain.types import TradingDate, is_fund_code

__all__ = [
    "action_order_key",
    "load_fund_dividends",
    "scan_consolidations",
    "to_etf_corporate_actions",
    "trading_days_span",
]

logger = logging.getLogger(__name__)

#: 双证据容差缺省（ADR-20 第 5 条 / 21 号 §4.13 `--tolerance`）
DEFAULT_TOLERANCE = 0.005
#: 折算候选判据（19 号 §91.3-B）
JUMP_THRESHOLD = 0.12      # |close/prev_close − 1| > 12%
EX_ABSORB_THRESHOLD = 0.05  # |close/pre_close − 1| < 5%
#: memo（进程内；键含 symbols 元组与容差——网格/复跑同区间零重复读）
_FUND_DIV_CACHE: dict[tuple, tuple] = {}
_CONS_SCAN_CACHE: dict[tuple, tuple] = {}


def trading_days_span(root: pathlib.Path | str, start_ymd: str,
                      end_ymd: str) -> list[str]:
    """`trade_cal` 交易日（is_open=1）升序清单；日历缺失 ⇒ 显式报错（禁静默）。"""
    from btf.data.core import YearTableStore

    t = YearTableStore(root).read_file("trade_cal")
    if t is None:
        raise FileNotFoundError(
            f"交易日历缺失（trade_cal）——ETF 公司行动（pay_date 顺延）拒绝静默跳过："
            f"{pathlib.Path(root) / 'metadata' / 'trade_cal.parquet'}")
    import pyarrow.compute as pc

    t = t.filter(pc.equal(t.column("is_open"), 1))
    days = sorted(str(d) for d in t.column("cal_date").to_pylist()
                  if d is not None and start_ymd <= str(d) <= end_ymd)
    return days


def _next_trading_day(days: list[str], ymd: str) -> str:
    """≥ ymd 的首个交易日（bisect；超出日历末尾 ⇒ 原样返回并标注）。"""
    idx = bisect.bisect_left(days, ymd)
    return days[idx] if idx < len(days) else ymd


def _fund_symbols(symbols: Iterable[str]) -> list[str]:
    return sorted({s for s in symbols if is_fund_code(s)})


def action_order_key(a: CorporateAction) -> tuple:
    """**R45′（N49）＋R53′（N57）**：同日同标的**排序键——先折算（0）后分红（1）**。

    ADR-20 第 ⑩ 条：折算日伴随除息时，分红现金应按**折算后**份额计
    （`qty_at_ex` 取当日折算完成后的持仓）；池内 0 实例（边界定义）。

    **作用域限定（R53′/N57，2026-10-08）**：ADR-20 第 ⑩ 条**只为 ETF/LOF 而立**
    ——非基金标的（股票侧 `corp_action_probe` 行动）第三键**恒为 1**，不扩权
    （相对次序＝输入序，与 R49′ 前逐位一致）；如需扩至股票侧须**先裁决**。

    **比率归一化（R54′，§102.8；勘误 #17）**：经 `ratio_or_default`——
    `None`／不可转换（`""`／`"abc"`）⇒ 1.0（后者 ＋WARN，禁崩）；
    **`≤0` 视为无效值 ⇒ 不建模 ⇒ 归「非折算类」（第三键 1）**。
    """
    if not is_fund_code(str(a.symbol)):
        return (a.ex_date, a.symbol, 1)
    # **R54′（N58/N59 合并）**：经统一归一化——`""`/`"abc"` 取 1.0＋WARN（禁崩）；
    # `≤0` 视为无效值（不建模）⇒ 归「非折算类」（第三键 1）
    cons = ratio_or_default(getattr(a, "consolidation_ratio", None),
                            context=f"order_key:{a.symbol}",
                            warn=logger.warning)
    return (a.ex_date, a.symbol, 0 if cons != 1.0 else 1)


def load_fund_dividends(
    root: pathlib.Path | str,
    symbols: Sequence[str],
    start: TradingDate,
    end: TradingDate,
    *,
    calendar: Sequence[str] | None = None,
    need_calendar: bool = True,
    stats: dict[str, Any] | None = None,
) -> tuple[list[CorporateAction], list[str]]:
    """`fund_div` → 分红事件（R32′）。返回 `(events, notes)`。

    `notes` 为披露文案（回退链 / 非交易日顺延 / 去重计数）；无事件 ⇒
    events 空、notes 空（披露不得恒现）。
    **R47′（N51，2026-10-08）**：`need_calendar=False` ⇒ **不读 `trade_cal`**
    （跳过 pay_date 顺延）——供**折算扫描路径**只取 `(ts_code, ex_date)` 集合用
    （交叉判别不需要日历；R43′ 引入的硬依赖由此去除）。
    **R51′（N55，2026-10-08）**：`stats`（可选出参）回填装载三元计数——
    `table_missing`／`rows_raw`／`n_proc_filtered`／`n_cash_filtered`／
    `n_out_of_window`／`n_dup`／`n_events`（供交叉防线 fail-safe 判定与披露）。
    """
    funds = _fund_symbols(symbols)
    if not funds:
        return [], []
    s0, s1 = start.to_ymd(), end.to_ymd()
    cal = list(calendar) if calendar is not None else None
    # **R47′（N51）**：`need_calendar` 入缓存键——两模式的顺延语义不同
    # （否则「先扫描后回测」会用无顺延结果污染运行路径）。
    key = (str(root), tuple(funds), s0, s1, bool(need_calendar))
    cached = _FUND_DIV_CACHE.get(key)
    if cached is not None:
        if stats is not None:
            stats.update(cached[2])
        return list(cached[0]), list(cached[1])

    from btf.data.core import YearTableStore

    store = YearTableStore(root)
    cols = ["ts_code", "ex_date", "pay_date", "earpay_date", "record_date",
            "ann_date", "div_proc", "div_cash"]
    # **R48′（N52，2026-10-08）**：`peek_columns` 在**表目录缺失**时抛
    # `ReaderError`（`_list_year_files` 拒绝静默）——原实现未捕获 ⇒ R44′
    # 告警分支走不到（N52 缺口）。此处捕获 ⇒ 等同「表缺失」，走同一告警分支。
    try:
        have = set(store.peek_columns("fund_div"))
    except Exception as exc:               # ReaderError（目录缺失）等
        logger.warning("fund_div 列探测失败（视为表缺失）：%s: %s",
                       type(exc).__name__, exc)
        have = set()
    events: list[CorporateAction] = []
    notes: list[str] = []
    if not have:
        # **R44′（N47，2026-10-08）**：表**缺失/不可读** ⇒ 显式告警＋披露
        # （**禁静默**——对齐 `trade_cal` 的「拒绝静默跳过」；N29 换触发条件：
        # dataset freeze / 离线快照漏带 `fund_div` 时不得静默漏计分红）。
        msg = ("⚠ 基金分红装载：`fund_div` 表**缺失/不可读**（含目录缺失）"
               " ⇒ **ETF/LOF 分红未装载**（禁静默——ADR-20/R44′；如为 "
               "dataset freeze／离线快照漏带，请补表后重跑）")
        logger.warning(msg)
        notes.append(msg)
        st = {"table_missing": True, "rows_raw": 0, "n_proc_filtered": 0,
              "n_cash_filtered": 0, "n_out_of_window": 0, "n_dup": 0,
              "n_events": 0}
        _FUND_DIV_CACHE[key] = ((), tuple(notes), dict(st))
        if stats is not None:
            stats.update(st)
        return events, notes
    cols = [c for c in cols if c in have or c == "ts_code"]
    t = store.read_codes("fund_div", funds, s0, s1, columns=cols)
    if t is None or not t.num_rows:
        # **R44′（N47）**：表在、区间内 0 条 ⇒ 披露（0 笔是事实，不静默）
        notes.append(
            f"基金分红装载：`fund_div` 在 {s0}~{s1} 内 **0 条记录**（表在）——"
            f"如为快照漏带请补表（R44′）")
        st = {"table_missing": False, "rows_raw": 0, "n_proc_filtered": 0,
              "n_cash_filtered": 0, "n_out_of_window": 0, "n_dup": 0,
              "n_events": 0}
        _FUND_DIV_CACHE[key] = ((), tuple(notes), dict(st))
        if stats is not None:
            stats.update(st)
        return events, notes

    import pyarrow.compute as pc

    # **R51′（N55）**：三元计数（原始行数／过滤跳过数／最终入账数）——
    # 「有行但过滤后为空」（E3）必须**同样披露**（原实现零告警静默）。
    raw_rows = t.num_rows
    if "div_proc" in t.column_names:
        t = t.filter(pc.equal(t.column("div_proc"), "实施"))
    n_proc_filtered = raw_rows - t.num_rows
    rows = t.to_pylist()
    rows.sort(key=lambda r: (str(r.get("ex_date") or ""), str(r.get("ts_code"))))
    seen: set[tuple[str, str]] = set()
    n_dup = 0
    n_fallback = 0
    n_rolled = 0
    n_cash_filtered = 0
    n_out_of_window = 0
    fallback_samples: list[str] = []
    rolled_samples: list[str] = []
    for r in rows:
        ex = str(r.get("ex_date") or "")
        sym = str(r.get("ts_code") or "")
        if not ex or not sym or not (s0 <= ex <= s1):
            n_out_of_window += 1
            continue
        dkey = (sym, ex)
        if dkey in seen:            # 去重：同日重复公告（510310 20241021 两行）
            n_dup += 1
            continue
        seen.add(dkey)
        div_cash = float(r.get("div_cash") or 0.0)
        if div_cash <= 0:
            # 送转类/无现金行：份额不变、无现金——不入账（ETF 分红以现金为准）
            n_cash_filtered += 1
            continue
        pay: str
        if r.get("pay_date"):
            pay = str(r["pay_date"])
        elif r.get("earpay_date"):
            pay = str(r["earpay_date"])
            n_fallback += 1
            if len(fallback_samples) < 3:
                fallback_samples.append(f"{sym} {ex}→{pay}")
        else:
            pay = ex
            n_fallback += 1
            if len(fallback_samples) < 3:
                fallback_samples.append(f"{sym} {ex}→ex(缺 pay/earpay)")
        # `pay_date` 非交易日 ⇒ 顺延至下一交易日（须披露）
        # **R47′（N51）**：`need_calendar=False` ⇒ **跳过顺延、不读 trade_cal**
        # （扫描路径只消费 ex_date 集合；缺日历不再抛未捕获异常）。
        if not need_calendar:
            eff = pay
        else:
            if cal is None:
                cal = trading_days_span(root, s0, s1)
            eff = _next_trading_day(cal, pay)
        if eff != pay:
            n_rolled += 1
            if len(rolled_samples) < 3:
                rolled_samples.append(f"{sym} {pay}→{eff}")
        events.append(CorporateAction(
            symbol=sym,
            ex_date=TradingDate.from_ymd(ex),
            pay_date=TradingDate.from_ymd(eff),
            record_date=(TradingDate.from_ymd(str(r["record_date"]))
                         if r.get("record_date") else None),
            cash_div_per_share=div_cash,
            stk_div_per_share=0.0,
            ann_date=(TradingDate.from_ymd(str(r["ann_date"]))
                      if r.get("ann_date") else None),
        ))
    if n_dup:
        notes.append(
            f"基金分红去重（ADR-20 第 4 条）：`(ts_code, ex_date)` 重复行 {n_dup} 条"
            f"已合并（仅入账一次）")
    if n_fallback:
        notes.append(
            f"基金分红 pay_date 回退（ADR-20 第 2 条）：{n_fallback} 笔走回退链 "
            f"`pay_date→earpay_date→ex_date`（样例：{'；'.join(fallback_samples)}）")
    if n_rolled:
        notes.append(
            f"基金分红 pay_date 非交易日顺延（ADR-20 第 2 条）：{n_rolled} 笔"
            f"顺延至下一交易日（样例：{'；'.join(rolled_samples)}）")
    if raw_rows > 0 and not events:
        # **R51′（N55-E3）**：「有行但过滤后为空」——原实现**零告警静默**；
        # 此形态下除息日交叉判别**无输入**，折算候选将按幅度阈值保留 ⇒ 必须披露。
        notes.append(
            f"⚠ 基金分红装载（**有行但过滤后为空**，R51′/N55）：`fund_div` 区间内"
            f"原始 **{raw_rows} 行** → `div_proc` 过滤 **{n_proc_filtered} 行**／"
            f"`div_cash<=0` 过滤 **{n_cash_filtered} 行**／区间外 {n_out_of_window} 行"
            f" ⇒ **最终入账 0 笔**；**除息日交叉判别无输入**——折算候选若存在将按"
            f"幅度阈值保留（请人工核对 `fund_div` 是否含未实施/无现金行）")
    events.sort(key=lambda a: (a.ex_date, a.symbol))
    st = {"table_missing": False, "rows_raw": raw_rows,
          "n_proc_filtered": n_proc_filtered,
          "n_cash_filtered": n_cash_filtered,
          "n_out_of_window": n_out_of_window, "n_dup": n_dup,
          "n_events": len(events)}
    _FUND_DIV_CACHE[key] = (tuple(events), tuple(notes), dict(st))
    if stats is not None:
        stats.update(st)
    return events, notes


def scan_consolidations(
    root: pathlib.Path | str,
    symbols: Sequence[str],
    start: TradingDate,
    end: TradingDate,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    cross_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """ETF/LOF 份额折算扫描（R34′ 判据；R35′ 事件源）。返回 `(events, notes)`。

    **R51′（N55）**：`cross_state`（可选出参）回填交叉防线状态——
    `disabled`（`fund_div` 表缺失/不可读 ⇒ **交叉判别不可用**，调用方应置
    `degraded`／CLI 退出码 2）＋`stats`（装载三元计数，见 `load_fund_dividends`）。

    事件字段（21 号 §4.13 输出契约）：`ts_code / date / R / evidence1（pre_close÷前收）
    / evidence2（f_{t−1}÷f_t，因子跳变反推）/ dev（|R×ρ−1|）/ ok（双证据通过）`。
    双证据不符 / 因子缺失 ⇒ `ok=False`（**fail-closed——不建模**，进未建模披露）。
    """
    funds = _fund_symbols(symbols)
    if not funds:
        return [], []
    s0, s1 = start.to_ymd(), end.to_ymd()
    key = (str(root), tuple(funds), s0, s1, float(tolerance))
    cached = _CONS_SCAN_CACHE.get(key)
    if cached is not None:
        return [dict(x) for x in cached[0]], list(cached[1])

    from btf.data.core import YearTableStore

    store = YearTableStore(root)
    # **R43′（N46，2026-10-08）**：折算候选与 `fund_div` **除息日交叉**——分红与
    # 折算在双证据上**数学同构**（除息日 `f_t = f_prev ÷ R` ⇒ `R × ρ = 1`，不可
    # 区分），`|jump| > 12%` 是唯一防线且**无语义依据**（全库可比分红 >12% 共
    # **218 笔**、最高 70.47%＝184721.SZ 2008-04-03 ⇒ 会被误建成折算：份额凭空
    # ×3.39 ＋ 分红同时入账＝**双重错误**）。⇒ **同日存在除息记录 ⇒ 判为分红、
    # 不计折算**（语义优先于幅度）。`load_fund_dividends` 走进程内 memo，零重复读。
    # **R47′（N51，2026-10-08）**：`need_calendar=False` ⇒ 扫描**不读
    # `trade_cal`**（交叉只需 (ts_code, ex_date) 集合；原实现缺日历即抛
    # 未捕获 FileNotFoundError——缺任一表即崩的新耦合由此去除）。
    _div_stats: dict[str, Any] = {}
    _divs_for_cross, _cross_div_notes = load_fund_dividends(
        root, funds, start, end, need_calendar=False, stats=_div_stats)
    div_ex = {(a.symbol, a.ex_date.to_ymd()) for a in _divs_for_cross}
    if cross_state is not None:
        # **R51′（N55）**：表缺失/不可读 ⇒ 交叉防线**不可用**（失能标志上报，
        # 由调用方升级为 degraded/退出码 2——与 trade_cal 同强度，禁静默放行）。
        cross_state["disabled"] = bool(_div_stats.get("table_missing"))
        cross_state["stats"] = dict(_div_stats)
    daily = store.read_codes("fund_daily", funds, s0, s1,
                             columns=["ts_code", "trade_date", "pre_close",
                                      "close"])
    adj = store.read_codes("fund_adj", funds, s0, s1,
                           columns=["ts_code", "trade_date", "adj_factor"])
    dcols: dict[str, dict[str, tuple]] = {}
    if daily is not None:
        cols = {n: daily.column(n).to_pylist() for n in daily.column_names}
        for i in range(daily.num_rows):
            sym = str(cols["ts_code"][i])
            d = str(cols["trade_date"][i])
            dcols.setdefault(sym, {})[d] = (cols["pre_close"][i],
                                            cols["close"][i])
    fcols: dict[str, dict[str, float]] = {}
    if adj is not None:
        cols = {n: adj.column(n).to_pylist() for n in adj.column_names}
        for i in range(adj.num_rows):
            sym = str(cols["ts_code"][i])
            d = str(cols["trade_date"][i])
            f = cols["adj_factor"][i]
            if f is not None:
                fcols.setdefault(sym, {})[d] = float(f)

    events: list[dict[str, Any]] = []
    notes: list[str] = []
    n_fail = 0
    n_crossed = 0
    cross_samples: list[str] = []
    fail_samples: list[str] = []
    for sym in funds:
        by_day = dcols.get(sym) or {}
        days = sorted(by_day)
        factors = fcols.get(sym) or {}
        for j in range(1, len(days)):
            d, dprev = days[j], days[j - 1]
            pre_close, close = by_day[d]
            prev_close = by_day[dprev][1]
            if not pre_close or not close or not prev_close:
                continue
            jump = close / prev_close - 1.0
            if abs(jump) <= JUMP_THRESHOLD:
                continue
            if abs(close / pre_close - 1.0) >= EX_ABSORB_THRESHOLD:
                continue        # 真行情（除权价未吸收）⇒ 非份额事件
            if (sym, d) in div_ex:
                # **R43′（N46）**：同日有除息记录 ⇒ 分红（语义优先于幅度）
                n_crossed += 1
                if len(cross_samples) < 3:
                    cross_samples.append(f"{sym} {d}")
                continue
            r = pre_close / prev_close
            f_t, f_prev = factors.get(d), factors.get(dprev)
            # **R39′（N41，2026-10-08）**：判「缺失」用 `is not None`——
            # 真值判断会把 `adj_factor=0.0` 脏行误判为「因子缺失」；
            # 脏行（≤0）仍 **fail-closed**，但证据 1/偏差照算（`dev=1.0`
            # 可读、归因可辨），不静默取其一（ADR-20 第 5 条）。
            if f_t is not None and f_prev is not None:
                rho = (f_t / f_prev) if f_prev else None
                ev2 = (f_prev / f_t) if f_t else None
                dev = (abs(r * rho - 1.0) if rho is not None else None)
                ok = bool(dev is not None and dev < tolerance)
            else:
                ev2, dev, ok = None, None, False   # 因子缺失 ⇒ fail-closed
            ev = {"ts_code": sym, "date": d, "R": round(r, 6),
                  "evidence1": round(r, 6),
                  "evidence2": (round(ev2, 6) if ev2 is not None else None),
                  "dev": (round(dev, 6) if dev is not None else None),
                  "ok": bool(ok)}
            events.append(ev)
            if not ok:
                n_fail += 1
                if len(fail_samples) < 3:
                    fail_samples.append(
                        f"{sym} {d}（R={r:.5f}，dev="
                        f"{'因子缺失' if dev is None else f'{dev:.4%}'}）")
    events.sort(key=lambda e: (e["date"], e["ts_code"]))
    if n_crossed:
        notes.append(
            f"份额折算候选**除息日交叉剔除**（N46/R43′）：{n_crossed} 个事件同日"
            f"存在 `fund_div` 除息记录 ⇒ 判为**分红**、不计折算（语义优先于幅度"
            f"阈值；样例：{'；'.join(cross_samples)}）")
    if n_fail:
        notes.append(
            f"⚠ 份额折算**未建模项（fail-closed）**：{n_fail} 个事件双证据不符或"
            f"因子缺失 ⇒ **不建模**（不得静默取其一——ADR-20 第 5 条）；样例："
            f"{'；'.join(fail_samples)}")
    # R44′：分红装载披露（含表缺失告警）先于扫描自身披露——CLI 扫描路径同样可见；
    # 运行路径由 `to_etf_corporate_actions` 合并去重。
    notes = [*_cross_div_notes, *notes]
    _CONS_SCAN_CACHE[key] = (tuple(events), tuple(notes))
    return [dict(x) for x in events], notes


def to_etf_corporate_actions(
    root: pathlib.Path | str,
    symbols: Sequence[str],
    start: TradingDate,
    end: TradingDate,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    calendar: Sequence[str] | None = None,
) -> tuple[list[CorporateAction], list[str], dict[str, Any]]:
    """ETF 公司行动 → `CorporateAction` 列表（引擎装载源）。

    返回 `(actions, notes, summary)`；`summary` 供报告「公司行动覆盖段」：
    `n_div / n_conv / n_unmodeled / n_symbols`。
    """
    funds = _fund_symbols(symbols)
    divs, div_notes = load_fund_dividends(root, funds, start, end,
                                          calendar=calendar)
    cons, cons_notes = scan_consolidations(root, funds, start, end,
                                           tolerance=tolerance)
    out = list(divs)
    for ev in cons:
        if not ev.get("ok"):
            continue
        r = float(ev["R"])
        if r <= 0:
            continue
        out.append(CorporateAction(
            symbol=str(ev["ts_code"]),
            ex_date=TradingDate.from_ymd(str(ev["date"])),
            pay_date=None,
            record_date=None,
            cash_div_per_share=0.0,
            stk_div_per_share=0.0,
            ann_date=None,
            consolidation_ratio=1.0 / r,       # 与股票缩股同构（qty ×= ratio）
            consolidation_source="fund_conv",
        ))
    # **R45′（N49，2026-10-08）**：同日同标的**顺序＝先折算后分红**
    # （ADR-20 第 ⑩ 条；排序键见 `_action_order_key`）——分红现金按**折算后**
    # 份额计；池内 0 实例（边界定义，池内探针交叉无命中）。
    out.sort(key=action_order_key)
    summary = {
        "n_symbols": len(funds),
        "n_div": len(divs),
        "n_conv": sum(1 for e in cons if e.get("ok")),
        "n_unmodeled": sum(1 for e in cons if not e.get("ok")),
    }
    # R44′/R43′：同一披露可能同时来自分红装载与折算扫描（共用 memo）⇒ 去重（保序）
    notes = list(dict.fromkeys([*div_notes, *cons_notes]))
    return out, notes, summary
