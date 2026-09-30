# -*- coding: utf-8 -*-
"""黄金数据集构建器（05 §20.2 / 09 §14.4；M2 任务 5.6）。

    python tools/build_golden.py --list
    python tools/build_golden.py --cases g1             # 构建（默认 golden_v1）
    python tools/build_golden.py --cases g1 --verify    # 只校验（不写）
    python tools/build_golden.py --cases g1 --force     # 显式覆盖（留痕）

产出：`{DATASETS_DIR}/golden_v{N}/cases/{case_id}.json` —— 含**截取数据**
（bars/actions/instruments/dates）+ plan（确定性输入）+ assertions（独立
参考计算器 `golden_refcalc.py` 产出的期望值）+ data_hash。

纪律：
    1. **只增不改**（05 §20.1）：case 文件已存在时——data_hash 与 assertions
       均一致 → 跳过（unchanged）；任一不同 → **报错退出**（断言修正=黄金集
       升版 golden_v{N+1}，或显式 `--force` 覆盖并留痕）；
    2. 参考计算器 **不 import btf**（独立实现，09 §14.3）；
    3. 案例**确定性**：规格锚定具体 symbol/ex_date，构建可重复（同主库同输出）。

已实现案例（G1 除权除息，第一断言=名义入账四式，09 §14.2）：
    g1_cash_div_same_day   派息 pay≡ex（2020+ 常态）
    g1_cash_div_ex_ne_pay  派息 ex≠pay（老数据，跨窗口两时点）
    g1_stk_div_mixed       送转+派息混合（合成式成本调整）
G2–G9（2026-09-27 补齐）：
    G2 涨跌停   g2_limit_up_buy_rejected / g2_limit_down_sell_rejected
    G3 停牌     g3_suspension_buy_rejected（长停牌期无 bar → 拒单 suspended）
    G4 T+1      g4_t1_same_day_sell_rejected / g4_t0_etf_same_day_roundtrip
    G5 ST       g5_st_interval（状态面板 is_st 区间断言）
    G6 退市     g6_delist_last_day（末日行情 + 退市后拒单 + 残值携带）
    G7 手数费用 g7_lot_and_min_commission / g7_fee_segments_pre2015
    G8 日历     g8_calendar_spring_festival（缺口 + W-FRI 周锚）
    G9 复权链   g9_dividend_chain_hfq（事件链收益 ≡ hfq 收益）
G10（2026-09-27 v0.5 V5-6 落地后补建，VPP 成交量参与率上限）：
    g10_vpp_partial_fill          vol×5% 够一手 → 部分成交，余量当日取消
    g10_vpp_volume_cap_rejected   vol×5% < 一手 → volume_cap 完全拒单

断言语义（`assertions` 按组扩展，均为**独立复算**，不复用引擎逻辑）：
    rejections / rejected_counts —— 拒单链（G2/G3/G4/G6/G7/G10）
    calendar                     —— 交易日历 + 缺口 + 周锚（G8）
    state_expectations           —— is_st 逐日期望（G5）
    hfq                          —— hfq 区间收益（G9）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TOOLS_DIR.parent
for _p in (str(_PROJECT_ROOT), str(_TOOLS_DIR)):   # btf 包根 + tools/ 内互引
    if _p not in sys.path:
        sys.path.insert(0, _p)
from golden_refcalc import compute_case

try:                                     # 主库读取（仅构建期；参考计算器不依赖）
    from btf.config.paths import DATASETS_DIR, MARKET_DATA_DIR
    from btf.data.parquet_reader import read_full, read_range
except ImportError:                      # pragma: no cover - 环境缺失时降级
    DATASETS_DIR = Path("btf_datasets")
    MARKET_DATA_DIR = Path("market_data")
    read_full = read_range = None  # type: ignore[assignment]

GOLDEN_VERSION = "v1"
CONTRACT_VERSION = "golden.v1"

#: 案例规格（锚定真实主库记录，确定性；window=anchor 前/后交易日数）
#:
#: 规格字段：
#:     symbol/anchor/before/after —— 窗口截取（anchor=窗口锚点交易日）
#:     span（可选）—— 显式区间 [from, to]（停牌/日历/复权链案例用，替代 anchor）
#:     with_states —— 提取状态面板（stk_limit/etf_limit/suspend_d/namechange/stock_basic）
#:     plan —— "targets"（G1 口径）| "orders"（显式订单，at=窗口内相对索引）| "none"
#:     asset_class/etf_subclass/t_plus —— 非股票标的（ETF T+0 案例）
#:     lot_size —— 标的整手（缺省 100）
CASE_SPECS: dict[str, dict[str, Any]] = {
    "g1_cash_div_same_day": {
        "group": "G1",
        "description": "派息且 pay≡ex（2020+ 常态）：单日 Δ=0，avg_cost 摊薄",
        "symbol": "000001.SZ", "anchor_ex_date": "20200528",
        "before": 3, "after": 7, "weight": 0.9,
    },
    "g1_cash_div_ex_ne_pay": {
        "group": "G1",
        "description": "派息 ex≠pay（600000.SH 2012）：ex 日缺口 / pay 日到账，窗口合并 Δ=0",
        "symbol": "600000.SH", "anchor_ex_date": "20120626",
        "before": 3, "after": 7, "weight": 0.9,
    },
    "g1_stk_div_mixed": {
        "group": "G1",
        "description": "送转+派息混合（stk 0.6）：合成式 avg_cost'=(avg−cash)/(1+stk)",
        "symbol": "000001.SZ", "anchor_ex_date": "20130620",
        "before": 3, "after": 7, "weight": 0.9,
    },
    # ── G2 涨跌停（stk_limit 真实限价 + 拒单链）──
    "g2_limit_up_buy_rejected": {
        "group": "G2",
        "description": "涨停开盘买入拒单：000004.SZ 20190411 开盘=涨停价 26.22（非停牌日）→ limit_up",
        "symbol": "000004.SZ", "anchor": "20190411",
        "before": 3, "after": 5, "with_states": True, "plan": "orders",
        "dates_from": "calendar",
        "orders": [{"at": 2, "side": "buy", "qty": 1000}],
    },
    "g2_limit_down_sell_rejected": {
        "group": "G2",
        "description": "跌停开盘卖出拒单：000010.SZ 20190429 起连续跌停（买允许、卖 limit_down）",
        "symbol": "000010.SZ", "anchor": "20190429",
        "before": 3, "after": 6, "with_states": True, "plan": "orders",
        "dates_from": "calendar",
        "orders": [{"at": 2, "side": "buy", "qty": 1000},
                   {"at": 3, "side": "sell", "qty": 1000}],
    },
    # ── G3 停牌（长停牌期截面缺席 → suspended）──
    "g3_suspension_buy_rejected": {
        "group": "G3",
        "description": "长期停牌：000006.SZ 20180102–20180308 无行情 → 停牌期内买入拒单；复牌后可成交",
        "symbol": "000006.SZ", "span": ["20171225", "20180320"],
        "dates_from": "calendar", "with_states": True, "plan": "orders",
        "orders": [{"date": "20180103", "side": "buy", "qty": 1000},
                   {"date": "20180309", "side": "buy", "qty": 1000}],
    },
    # ── G4 T+1 / T+0（次日开盘卖：股票拒、债券 ETF 成交）──
    "g4_t1_same_day_sell_rejected": {
        "group": "G4",
        "description": "T+1：同日决策买+卖（股票 t_plus=1）→ 买成交、卖 T_PLUS_1 拒单",
        "symbol": "000001.SZ", "anchor": "20200601",
        "before": 3, "after": 5, "with_states": True, "plan": "orders",
        "orders": [{"at": 1, "side": "buy", "qty": 1000},
                   {"at": 1, "side": "sell", "qty": 1000}],
    },
    "g4_t0_etf_same_day_roundtrip": {
        "group": "G4",
        "description": "T+0：可转债 ETF（511380.SH，子类 bond）同日买+卖**均成交**（E2 双维规则）",
        "symbol": "511380.SH", "anchor": "20210601",
        "before": 3, "after": 5, "with_states": True, "plan": "orders",
        "asset_class": "etf", "etf_subclass": "bond", "t_plus": 0,
        "price_table": "fund_daily",
        "orders": [{"at": 1, "side": "buy", "qty": 1000},
                   {"at": 1, "side": "sell", "qty": 1000}],
    },
    # ── G5 ST（namechange 区间 → 状态面板期望）──
    "g5_st_interval": {
        "group": "G5",
        "description": "ST 状态区间（namechange）：状态面板 is_st 逐日期望（锚点构建期扫描）",
        "st_scan_year": "2019", "before": 5, "after": 10,
        "with_states": True, "plan": "none",
    },
    # ── G6 退市（末日行情 + 退市后拒单 + 残值携带）──
    "g6_delist_last_day": {
        "group": "G6",
        "description": "退市 000033.SZ（最后交易日 20170706）：末日行情 + 退市后卖出拒单 + 残值按末价携带",
        "symbol": "000033.SZ", "anchor": "20170706",
        "before": 5, "after": 9, "dates_from": "calendar",
        "with_states": True, "plan": "orders",
        "orders": [{"at": 1, "side": "buy", "qty": 1000},
                   {"at": 7, "side": "sell", "qty": 1000}],
    },
    # ── G7 手数与费用（整手/最低佣金/费率分段 + 卖先买后资金链）──
    "g7_lot_and_min_commission": {
        "group": "G7",
        "description": "非整手 150 股拒单 + 1000 股成交触发最低佣金 5 元 + 卖先买后资金链",
        "symbol": "601668.SH", "anchor": "20210728",
        "before": 3, "after": 5, "with_states": True, "plan": "orders",
        "orders": [{"at": 0, "side": "buy", "qty": 150},
                   {"at": 0, "side": "buy", "qty": 1000},
                   {"at": 1, "side": "sell", "qty": 1000},
                   {"at": 1, "side": "buy", "qty": 1000}],
    },
    "g7_fee_segments_pre2015": {
        "group": "G7",
        "description": "费率分段（2015-08-01 前：过户费仅沪市按面额 0.06%）+ 最低佣金",
        "symbol": "600000.SH", "anchor": "20140701",
        "before": 3, "after": 5, "with_states": True, "plan": "orders",
        "orders": [{"at": 0, "side": "buy", "qty": 1000},
                   {"at": 1, "side": "sell", "qty": 1000}],
    },
    # ── G8 日历边界（春节缺口 + W-FRI 周锚）──
    "g8_calendar_spring_festival": {
        "group": "G8",
        "description": "日历边界（2021 春节）：最长缺口 ≥ 9 自然日 + 交易日 W-FRI 周锚归组",
        "symbol": "000001.SZ", "span": ["20210205", "20210226"],
        "dates_from": "calendar", "plan": "none",
    },
    # ── G9 复权链（事件链收益 ≡ hfq 收益）──
    "g9_dividend_chain_hfq": {
        "group": "G9",
        "description": "复权链（601988.SH 两次分红 20190603/20200715）：全持仓事件链收益 ≡ hfq 区间收益",
        "symbol": "601988.SH", "span": ["20190520", "20200831"],
        "dates_from": "calendar", "plan": "targets", "weight": 0.995,
        "hold_to_end": True, "hfq": True,
    },
    # ── G10 流动性边界（VPP 成交量参与率上限，v0.5 V5-6）──
    #    bars.vol 为主库 tushare 原始口径（手）——引擎夹具与参考计算器
    #    **同数消费**（两侧一致即对账有效；单位换算不改变断言语义）
    "g10_vpp_partial_fill": {
        "group": "G10",
        "description": "VPP 部分成交：601599.SH 20191108 vol=30000 × 5% = 1500 股 → 10 万股委托部分成交，余量当日取消",
        "symbol": "601599.SH", "anchor": "20191108",
        "before": 3, "after": 5, "with_states": True, "plan": "orders",
        "dates_from": "calendar", "volume_participation": 0.05,
        "orders": [{"at": 2, "side": "buy", "qty": 100000}],
    },
    "g10_vpp_volume_cap_rejected": {
        "group": "G10",
        "description": "VPP 量上限不足一手：600265.SH 20191126 vol=151 × 5% = 7 股 < 100 → volume_cap 拒单（完全无法成交）",
        "symbol": "600265.SH", "anchor": "20191126",
        "before": 3, "after": 5, "with_states": True, "plan": "orders",
        "dates_from": "calendar", "volume_participation": 0.05,
        "orders": [{"at": 2, "side": "buy", "qty": 100000}],
    },
}

#: 待补案例组（显式留痕——不静默假装覆盖；G1–G10 全部交付后为空）
PENDING_GROUPS: dict[str, str] = {}


# ─────────────────────────────────────────────────────────────
# 主库截取（可注入 reader 以便测试脱离主库）
# ─────────────────────────────────────────────────────────────
def _bars_from_mainlib(symbol: str, start: str, end: str, root: Path, *,
                       table: str = "daily") -> list[dict[str, Any]]:
    """日线表区间读取（股票 daily / ETF fund_daily）→ bar dict 列表（升序）。"""
    arrow = read_range(table, root, start, end,
                       columns=["ts_code", "trade_date", "open", "high", "low",
                                "close", "pre_close", "vol", "amount"])
    rows = [r for r in arrow.to_pylist() if r["ts_code"] == symbol]
    bars = [{
        "symbol": r["ts_code"], "date": r["trade_date"],
        "open": float(r["open"]), "high": float(r["high"]),
        "low": float(r["low"]), "close": float(r["close"]),
        "pre_close": float(r["pre_close"] or 0.0),
        "vol": float(r["vol"] or 0.0), "amount": float(r["amount"] or 0.0),
    } for r in rows if r["open"] is not None and r["close"] is not None]
    return sorted(bars, key=lambda b: (b["date"], b["symbol"]))


def _trading_dates(root: Path, start: str, end: str) -> list[str]:
    """交易日历（全市场 daily 的交易日并集，升序）。

    停牌日**仍在日历内**（无 bar）——`MemoryFeed(dates=…)` 依赖此语义。
    """
    arrow = read_range("daily", root, start, end, columns=["trade_date"])
    return sorted({r["trade_date"] for r in arrow.to_pylist()})


def _bars_reader(root: Path, spec: dict[str, Any]):
    """规格 → 日线读取闭包（price_table 决定 daily / fund_daily）。"""
    table = spec.get("price_table", "daily")

    def read(symbol: str, start: str, end: str, _root: Path):
        return _bars_from_mainlib(symbol, start, end, root, table=table)

    return read


def _states_from_mainlib(symbol: str, dates: list[str], root: Path
                         ) -> list[dict[str, Any]]:
    """状态面板逐日截取（**独立直读主库**，不复用引擎 StateSynthesizer）。

    限价：股票 `stk_limit` / ETF `etf_limit`（起点 2019-06-26）；
    停牌：`suspend_d`（当天在表即停牌）；ST：`namechange`（name 含 'ST'）；
    退市：`stock_basic.delist_date`。
    """
    if not dates:
        return []
    lo, hi = dates[0], dates[-1]
    limit_table = "etf_limit" if _price_table(symbol) != "daily" else "stk_limit"
    limits: dict[str, tuple[float | None, float | None]] = {}
    try:
        arrow = read_range(limit_table, root, lo, hi,
                           columns=["ts_code", "trade_date", "up_limit",
                                    "down_limit"])
        limits = {r["trade_date"]: (r["up_limit"], r["down_limit"])
                  for r in arrow.to_pylist() if r["ts_code"] == symbol}
    except Exception:                       # 表缺失（etf_limit 起点前）→ 无限价
        limits = {}
    suspended: set[str] = set()
    try:
        arrow = read_range("suspend_d", root, lo, hi,
                           columns=["ts_code", "trade_date"])
        suspended = {r["trade_date"] for r in arrow.to_pylist()
                     if r["ts_code"] == symbol}
    except Exception:
        suspended = set()
    st_days = _st_days(symbol, dates, root)
    delist = _delist_date(symbol, root)
    return [{
        "date": day, "symbol": symbol,
        "limit_up": limits.get(day, (None, None))[0],
        "limit_down": limits.get(day, (None, None))[1],
        "suspended": day in suspended,
        "is_st": day in st_days,
        "delisted": delist is not None and day >= delist,
    } for day in dates]


def _price_table(symbol: str) -> str:
    from btf.data.tables_meta import daily_table_of
    return daily_table_of(symbol)


def _st_days(symbol: str, dates: list[str], root: Path) -> set[str]:
    """ST 命中日（namechange 区间：name 含 'ST' ∧ start ≤ d < end）。"""
    arrow = read_full("namechange", root,
                      columns=["ts_code", "name", "start_date", "end_date"])
    spans = [(r["start_date"], r["end_date"]) for r in arrow.to_pylist()
             if r["ts_code"] == symbol and "ST" in (r.get("name") or "")]
    return {day for day in dates
            if any(start and start <= day and (not end or day < end)
                   for start, end in spans)}


def _delist_date(symbol: str, root: Path) -> str | None:
    arrow = read_full("stock_basic", root, columns=["ts_code", "delist_date"])
    for row in arrow.to_pylist():
        if row["ts_code"] == symbol:
            return row.get("delist_date") or None
    return None


def _hfq_return(symbol: str, dates: list[str], root: Path, *,
                price_table: str = "daily") -> dict[str, Any]:
    """hfq 区间收益（adj_factor **独立复算**，09 §14.2 G9 引擎侧等价断言用）。

    区间取 dates[1]（建仓次日，已重估）→ dates[-1]：此后无交易，现金仅因
    分红变化、份额仅因送转变化，故事件链收益应与 hfq 收益同值（容差 1%）。
    """
    if len(dates) < 3:
        raise RuntimeError(f"{symbol} 窗口过短，无法计算 hfq 区间收益")
    frm, to = dates[1], dates[-1]
    arrow = read_range("adj_factor", root, frm, to,
                       columns=["ts_code", "trade_date", "adj_factor"])
    adj = {r["trade_date"]: r["adj_factor"] for r in arrow.to_pylist()
           if r["ts_code"] == symbol and r["adj_factor"]}
    bars = {b["date"]: b["close"]
            for b in _bars_from_mainlib(symbol, frm, to, root, table=price_table)}
    missing = [d for d in (frm, to) if d not in adj or d not in bars]
    if missing:
        raise RuntimeError(f"{symbol} hfq 输入缺失（adj_factor/close）：{missing}")
    value_from = bars[frm] * float(adj[frm])
    value_to = bars[to] * float(adj[to])
    return {"from": frm, "to": to, "close_from": bars[frm], "close_to": bars[to],
            "adj_from": float(adj[frm]), "adj_to": float(adj[to]),
            "return": value_to / value_from - 1.0, "tolerance": 0.01}


def _actions_from_mainlib(symbol: str, start: str, end: str,
                          root: Path) -> list[dict[str, Any]]:
    """dividend 表 → CorporateAction 形态（区间型表：read_full + 自行判定）。"""
    table = read_full("dividend", root,
                      columns=["ts_code", "ann_date", "ex_date", "pay_date",
                               "record_date", "cash_div", "stk_div"])
    out = []
    for r in table.to_pylist():
        if r["ts_code"] != symbol:
            continue
        ex = r["ex_date"]
        if not ex or ex < start or ex > end:
            continue
        out.append({
            "symbol": r["ts_code"],
            "ex_date": ex,
            "pay_date": r["pay_date"] or ex,        # 缺失回退 ex（E5）
            "record_date": r["record_date"],
            "cash_div_per_share": float(r["cash_div"] or 0.0),
            "stk_div_per_share": float(r["stk_div"] or 0.0),
            "ann_date": r["ann_date"],
        })
    return sorted(out, key=lambda a: a["ex_date"])


def _window_dates(dates: list[str], anchor: str, before: int,
                  after: int) -> list[str]:
    """以 anchor 为锚取窗口交易日（anchor 缺失 → 取最近交易日）。"""
    ordered = sorted(set(dates))
    if not ordered:
        return []
    if anchor in ordered:
        idx = ordered.index(anchor)
    else:
        idx = min(range(len(ordered)),
                  key=lambda i: abs(int(ordered[i]) - int(anchor)))
    return ordered[max(0, idx - before):min(len(ordered), idx + after + 1)]


def _instrument_row(spec: dict[str, Any]) -> dict[str, Any]:
    """案例标的一行（G1 口径逐字段不变；G3–G9 追加 t_plus/etf_subclass）。"""
    symbol = spec["symbol"]
    asset = spec.get("asset_class", "stock")
    row: dict[str, Any] = {
        "symbol": symbol, "asset_class": asset,
        "board": spec.get("board", "etf" if asset == "etf" else "main"),
        "lot_size": int(spec.get("lot_size", 100)),
    }
    if spec.get("etf_subclass"):
        row["etf_subclass"] = spec["etf_subclass"]
    if "t_plus" in spec:
        row["t_plus"] = int(spec["t_plus"])
    return row


def _resolve_st_anchor(spec: dict[str, Any], root: Path) -> dict[str, Any]:
    """G5 锚点：扫描 namechange 取指定年份首个 ST 区间（确定性：按代码序）。"""
    year = spec["st_scan_year"]
    arrow = read_full("namechange", root,
                      columns=["ts_code", "name", "start_date", "end_date"])
    spans = sorted({
        (r["ts_code"], r["start_date"]) for r in arrow.to_pylist()
        if "ST" in (r.get("name") or "") and r.get("start_date")
        and r["start_date"][:4] == year
    })
    for code, start in spans:
        if _price_table(code) != "daily":
            continue
        bars = _bars_from_mainlib(code, _shift_ymd(start, -40),
                                  _shift_ymd(start, 60), root)
        if len(bars) >= 12:
            resolved = dict(spec)
            resolved["symbol"] = code
            resolved["anchor"] = start
            return resolved
    raise RuntimeError(f"G5 锚点扫描失败：{year} 年无可用 ST 区间（namechange）")


def _calendar_block(dates: list[str]) -> dict[str, Any]:
    """交易日历断言块（G8）：长缺口（自然日）+ W-FRI 周锚归组（**独立实现**）。"""
    from datetime import date as _date
    from datetime import timedelta

    def parse(ymd: str) -> _date:
        return _date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))

    gaps = []
    for prev, nxt in pairwise(dates):
        delta = (parse(nxt) - parse(prev)).days
        if delta > 3:
            gaps.append({"prev": prev, "next": nxt, "calendar_days": delta})
    weeks: dict[str, list[str]] = {}
    for day in dates:
        parsed = parse(day)
        week_end = (parsed + timedelta(days=(4 - parsed.weekday()) % 7))
        weeks.setdefault(week_end.strftime("%Y%m%d"), []).append(day)
    return {"dates": dates, "gaps": gaps,
            "week_anchors": dict(sorted(weeks.items()))}


def extract_case(spec: dict[str, Any], root: Path, *,
                 bars_reader: Callable[..., list[dict[str, Any]]] | None = None,
                 actions_reader: Callable[..., list[dict[str, Any]]] | None = None,
                 ) -> dict[str, Any]:
    """按规格从主库截取案例数据。

    窗口：`span=[from, to]` 显式区间，或 `anchor` 前后 N 个交易日；
    `dates_from="calendar"` 时交易日历取**全市场 daily 并集**（停牌日在日历内
    但无 bar——G3/G6/G8/G9 的关键语义）。
    """
    if "symbol" not in spec and "st_scan_year" in spec:
        spec = _resolve_st_anchor(spec, root)
    symbol = spec["symbol"]
    reader = bars_reader or _bars_reader(root, spec)
    if spec.get("span"):
        start, end = spec["span"]
        anchor = spec.get("anchor") or start
    else:
        anchor = spec.get("anchor") or spec.get("anchor_ex_date")
        start, end = _shift_ymd(anchor, -60), _shift_ymd(anchor, 60)
    bars = reader(symbol, start, end, root)
    if not bars:
        raise RuntimeError(f"{symbol} 在 {start}~{end} 无行情（主库缺失？）")
    if spec.get("dates_from") == "calendar":
        universe = _trading_dates(root, start, end)
    else:
        universe = [b["date"] for b in bars]
    if spec.get("span"):
        dates = universe            # 显式区间 = 整段交易日（不窗口化）
    else:
        dates = _window_dates(universe, anchor, spec["before"], spec["after"])
    if not dates:
        raise RuntimeError(f"{symbol} 窗口为空（{start}~{end} 无交易日）")
    window_bars = [b for b in bars if b["date"] in set(dates)]
    actions = (actions_reader or _actions_from_mainlib)(symbol, dates[0],
                                                        dates[-1], root)
    data: dict[str, Any] = {
        "dates": dates,
        "bars": window_bars,
        "actions": actions,
        "instruments": [_instrument_row(spec)],
    }
    if spec.get("with_states"):
        data["states"] = _states_from_mainlib(symbol, dates, root)
    return data


def _shift_ymd(ymd: str, days: int) -> str:
    """YYYYMMDD ± 自然日（窗口截取用；不依赖 trading calendar）。"""
    from datetime import date, timedelta
    d = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])) + timedelta(days=days)
    return d.strftime("%Y%m%d")


# ─────────────────────────────────────────────────────────────
# 构建 / 校验（只增不改）
# ─────────────────────────────────────────────────────────────
def _hash(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _same(left: Any, right: Any) -> bool:
    """NaN 容忍的深度等价（只增不改校验用）。

    零成交案例的指标含 NaN（sharpe/calmar/盈亏比）；JSON 往返后
    `nan != nan` → 原 `!=` 比较**误报漂移**（2026-09-27 G2 零成交案例实测）。
    """
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, float) and isinstance(right, float):
        return left == right or (math.isnan(left) and math.isnan(right))
    return left == right


def make_plan(spec: dict[str, Any], dates: list[str]) -> dict[str, Any]:
    """确定性计划：目标权重（首日建仓 / 倒数第二日清仓）与/或显式订单。

    - `plan="targets"`（缺省，G1 口径）：首日建仓 weight，**倒数第二日清仓**
      （清仓决策放倒数第二日——次日才撮合，置于末日则卖出落在窗口外）；
      `hold_to_end=True` 时不清仓（G9 复权链需全程持有）。
    - `plan="orders"`：显式订单；`at`=窗口内相对索引（决策日），`date`=绝对
      交易日（停牌案例用——窗口由日历决定，索引不便固定）。
    - `plan="none"`：无决策（G5/G8 只看状态与日历）。
    """
    symbol = spec["symbol"]
    mode = spec.get("plan", "targets")
    plan: dict[str, Any] = {"initial_cash": 1_000_000.0, "targets": {},
                            "lot_size": int(spec.get("lot_size", 100))}
    if spec.get("volume_participation"):
        plan["execution"] = {
            "volume_participation": float(spec["volume_participation"])}
    if mode == "targets" and dates:
        targets: dict[str, dict[str, float]] = {
            dates[0]: {symbol: spec.get("weight", 0.9)}}
        if len(dates) > 2 and not spec.get("hold_to_end"):
            targets[dates[-2]] = {}
        plan["targets"] = targets
    elif mode == "orders":
        plan["orders"] = [{
            "date": dates[draft["at"]] if "at" in draft else draft["date"],
            "symbol": draft.get("symbol", symbol),
            "side": draft["side"], "qty": int(draft["qty"]),
        } for draft in spec.get("orders", [])]
        if spec.get("weight"):
            plan["targets"] = {dates[0]: {symbol: spec["weight"]}}
    return plan


def build_case_dict(case_id: str, spec: dict[str, Any], data: dict[str, Any],
                    root: Path) -> dict[str, Any]:
    """案例数据 + plan + 参考计算器断言（+ 组专属断言块）→ 可落盘 case dict。"""
    plan = make_plan(spec, data["dates"])
    assertions = compute_case(data, plan)
    if spec.get("with_states"):
        states = data.get("states", [])
        assertions["state_expectations"] = {
            "symbol": spec["symbol"],
            "is_st": {s["date"]: s["is_st"] for s in states},
            "suspended": {s["date"]: s["suspended"] for s in states},
            "delisted": {s["date"]: s["delisted"] for s in states},
            "limits": {s["date"]: [s["limit_up"], s["limit_down"]]
                       for s in states},
        }
    if spec["group"] == "G8":
        assertions["calendar"] = _calendar_block(data["dates"])
    if spec.get("hfq"):
        assertions["hfq"] = _hfq_return(
            spec["symbol"], data["dates"], root,
            price_table=spec.get("price_table", "daily"))
    window: dict[str, Any] = {"dates": data["dates"]}
    if spec.get("span"):
        window["span"] = list(spec["span"])
    else:
        window["before"] = spec["before"]
        window["after"] = spec["after"]
    return {
        "case_id": case_id,
        "contract_version": CONTRACT_VERSION,
        "group": spec["group"],
        "description": spec["description"],
        "source": {
            "database": str(root),
            "symbol": spec["symbol"],
            "anchor": spec.get("anchor") or spec.get("anchor_ex_date"),
            "window": window,
        },
        "data": data,
        "plan": plan,
        "data_hash": _hash(data),
        "assertions": assertions,
    }


def build(case_id: str, out_dir: Path, *, root: Path, verify: bool = False,
          force: bool = False, **extract_kw) -> str:
    """构建单个 case（返回状态：created | unchanged | updated | verified）。"""
    spec = CASE_SPECS[case_id]
    if "symbol" not in spec and "st_scan_year" in spec:
        spec = _resolve_st_anchor(spec, root)     # G5：锚点构建期扫描（确定性）
    data = extract_case(spec, root, **extract_kw)
    case = build_case_dict(case_id, spec, data, root)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{case_id}.json"

    if not path.is_file():
        if verify:
            return f"{case_id}: 缺失（--verify 不写）"
        path.write_text(json.dumps(case, ensure_ascii=False, indent=2,
                                   sort_keys=True) + "\n", encoding="utf-8")
        return f"{case_id}: created"

    existing = json.loads(path.read_text(encoding="utf-8"))
    drift: list[str] = []
    if existing.get("data_hash") != case["data_hash"]:
        drift.append("data_hash（主库数据已变）")
    if not _same(existing.get("assertions"), case["assertions"]):
        drift.append("assertions（参考计算器结果不同）")
    if not drift:
        return f"{case_id}: unchanged"

    if verify or not force:
        raise GoldenAssertionDrift(
            f"{case_id} 已存在且发生漂移：{', '.join(drift)}——"
            f"只增不改纪律（05 §20.1）：请升版 golden_v{{N+1}} 或显式 --force")
    path.write_text(json.dumps(case, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")
    return f"{case_id}: updated（--force 覆盖，留痕）"


class GoldenAssertionDrift(RuntimeError):
    """断言漂移（只增不改纪律拦截）。"""


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def _select(cases: str) -> list[str]:
    """选择案例：all | 组名（g1…g9）| 逗号分隔的 case_id。"""
    if cases in ("", "all"):
        return sorted(CASE_SPECS)
    key = cases.strip().lower()
    if len(key) >= 2 and key[0] == "g" and key[1:].isdigit():
        group = key.upper()
        return [c for c in sorted(CASE_SPECS) if CASE_SPECS[c]["group"] == group]
    return [c.strip() for c in cases.split(",") if c.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="黄金数据集构建器（05 §20.2）")
    parser.add_argument("--cases", default="g1", help="案例（g1|all|逗号分隔 case_id）")
    parser.add_argument("--out", default=None, help="输出目录（默认 DATASETS_DIR/golden_v1）")
    parser.add_argument("--root", default=None, help="主库根（默认 MARKET_DATA_DIR）")
    parser.add_argument("--verify", action="store_true", help="只校验不写")
    parser.add_argument("--force", action="store_true", help="显式覆盖（留痕）")
    parser.add_argument("--list", action="store_true", help="列出案例规格与已构建状态")
    args = parser.parse_args(argv)

    # 中文/全角输出纪律（06 §11.3）：Windows GBK 控制台强制 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = Path(args.root) if args.root else MARKET_DATA_DIR
    out_dir = (Path(args.out) if args.out
               else DATASETS_DIR / f"golden_{GOLDEN_VERSION}" / "cases")

    if args.list:
        for case_id in sorted(CASE_SPECS):
            spec = CASE_SPECS[case_id]
            built = (out_dir / f"{case_id}.json").is_file()
            print(f"{case_id:24s} {spec['group']}  "
                  f"{'built' if built else 'missing':8s} {spec['description']}")
        print("-- pending（未实现，显式的）--")
        for group, desc in PENDING_GROUPS.items():
            print(f"{group:24s} {desc}")
        return 0

    if read_full is None:
        print("主库读取不可用（btf 未安装/pyarrow 缺失）", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"主库不可访问: {root}", file=sys.stderr)
        return 2

    ok = True
    for case_id in _select(args.cases):
        if case_id not in CASE_SPECS:
            print(f"未知案例 {case_id}（可选: {sorted(CASE_SPECS)}）",
                  file=sys.stderr)
            ok = False
            continue
        try:
            print(build(case_id, out_dir, root=root, verify=args.verify,
                        force=args.force))
        except GoldenAssertionDrift as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
