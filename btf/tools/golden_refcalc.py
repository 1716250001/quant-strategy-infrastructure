# -*- coding: utf-8 -*-
"""黄金集**独立参考计算器**（05 §20.2 / 09 §14.3；M2 任务 5.6）。

纪律（回归有效性的前提）：
    **不 import btf** —— 全部公式以显式循环重写一遍（记账四式、事件调整
    两时点、NAV 四式、分段费率、指标）。与引擎同规则但代码独立：两者一致
    才是有效回归；若引擎有 bug，参考计算器不会跟着错（09 §14.3「独立实现
    的参考计算器」假设的落地）。

覆盖（G1 除权除息第一断言=名义入账四式，09 §14.2）：
    记账四式（04 §8.2.6）：
        买 avg_cost' = (avg_cost×qty + price×fill_qty + fee) / (qty + fill_qty)
        卖 realized_pnl += (price − avg_cost) × qty − fee
        现 cash ∓ (price×qty ± fee)
        估 unrealized = (close − avg_cost) × qty
    事件调整（v0.3 N7-A 两时点）：
        ex：avg_cost −= cash_div；再 qty ×= (1+stk_div)、avg_cost /= (1+stk_div)
            （混合行动合成式 avg_cost' = (avg_cost − cash_div)/(1+stk_div)）
        pay：cash += qty_at_ex × cash_div（按**登记在册**数量，与窗口内卖出无关）
    NAV 四式（04 §8.2.6 N5-1）：
        total_value = cash + Σ qty×close；daily/cumulative/drawdown 三式
    撮合：T 日收盘决策 → T+1 **开盘价**成交（next_open，无滑点）；T+1 解冻
        （当日买入次日可卖）；差额法下单 + 整手向下取整 + 卖先买后。

扩展覆盖（M2 5.6 补全 G2–G9；**仍不 import btf**）：
    data.states（可选）——逐 (symbol, date) 状态面板：
        limit_up / limit_down（涨跌停价，G2）、suspended（停牌，G3）、
        is_st（G5，断言层消费）、delisted（G6）
    data.instruments 可带 t_plus（0=T+0：债券/黄金/跨境/货币 ETF；G4/B3）
    拒单链（顺序**镜像引擎** `NextOpenHandler._check`）：
        suspended → limit_up / limit_down → lot_size（买）→ t_plus_1（卖）→
        insufficient_cash（买）；结果进 assertions.rejections + rejected_counts
    plan.orders（可选）——显式订单（决策日 → 次日开盘撮合），用于 G4（当日
        买入当日卖拒单）/ G7（卖先买后资金链）；与 plan.targets 并用时顺序
        镜像引擎 `drafts = explicit + rebalancer(target)`
    估值基准：无当日 bar（停牌/退市后）沿用上一已知收盘价（镜像
        `Portfolio._last_close`），从未估值过才回退 avg_cost（G3/G6）

输入/输出：纯 dict（可 JSON 化），无 I/O、无第三方依赖。
"""
from __future__ import annotations

import math
from typing import Any

#: 过户费/印花税分段（与引擎 A_SHARE_SEGMENTS 同规则，**独立抄录**）
SEGMENTS: tuple[dict[str, Any], ...] = (
    {"from": "", "stamp_sell": 0.001, "transfer": 0.0006,
     "scope": "sh_only", "basis": "par"},
    {"from": "20150801", "stamp_sell": 0.001, "transfer": 0.00002,
     "scope": "both", "basis": "amount"},
    {"from": "20220429", "stamp_sell": 0.001, "transfer": 0.00001,
     "scope": "both", "basis": "amount"},
    {"from": "20230828", "stamp_sell": 0.0005, "transfer": 0.00001,
     "scope": "both", "basis": "amount"},
)
COMMISSION_RATE = 2.5e-4          # 万 2.5
COMMISSION_MIN = 5.0              # 单笔最低佣金（元）
PAR_VALUE = 1.0                   # A 股面值简化（元/股）
#: 涨跌停价比较容差（与引擎 `execution.handler.LIMIT_PRICE_TOL` 同值，**独立抄录**）
LIMIT_PRICE_TOL = 1e-4


def _instrument_meta(instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """标的市场属性（lot_size / t_plus；缺省 lot=100、t_plus=1——同引擎 BoardRule）。"""
    out: dict[str, dict[str, Any]] = {}
    for inst in instruments:
        out[inst["symbol"]] = {
            "lot_size": int(inst.get("lot_size") or 100),
            "t_plus": int(inst.get("t_plus", 1)),
        }
    return out


def _rejection(date: str, symbol: str, side: str, qty: int, code: str
               ) -> dict[str, Any]:
    """拒单记录（code 与引擎 `RejectCode` 取值同名小写）。"""
    return {"date": date, "symbol": symbol, "side": side, "qty": qty,
            "code": code}


def _segment(date: str) -> dict[str, Any]:
    """日期生效段（from ≤ date 的最晚段；首段 from='' 覆盖全程）。"""
    hit = None
    for seg in SEGMENTS:
        if seg["from"] <= date:
            hit = seg
    if hit is None:
        raise ValueError(f"费率表未覆盖 {date}")
    return hit


def fees_of(symbol: str, side: str, qty: int, price: float, date: str
            ) -> dict[str, float]:
    """费用分项（元，四舍五入至分；与引擎 AShareTieredFeeModel 同规则）。"""
    seg = _segment(date)
    turnover = qty * price
    commission = max(turnover * COMMISSION_RATE, COMMISSION_MIN)
    stamp = turnover * seg["stamp_sell"] if side == "sell" else 0.0
    if seg["scope"] == "sh_only" and not symbol.endswith(".SH"):
        transfer = 0.0
    elif seg["basis"] == "par":
        transfer = qty * PAR_VALUE * seg["transfer"]
    else:
        transfer = turnover * seg["transfer"]
    # 合计按**未取整分量之和**取整（镜像引擎 Fee.total 口径）——逐项取整后
    # 再求和会与引擎差 1 分（2021-05-31 511380.SH 实测：15.70 vs 15.71）
    return {"commission": round(commission, 2), "stamp_duty": round(stamp, 2),
            "transfer_fee": round(transfer, 2),
            "total": round(commission + stamp + transfer, 2)}


def _effective_dividend_qty(positions: dict[str, dict], symbol: str,
                            pending: list[dict]) -> int:
    """派息登记数量（qty_at_ex）——与窗口内卖出无关（v0.3 四次复核 C1）。"""
    return positions.get(symbol, {}).get("qty", 0)


def compute_case(data: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """独立复算一个黄金案例 → 断言 dict（snapshots/fills/events/metrics）。"""
    dates: list[str] = sorted(data["dates"])
    bars: dict[tuple[str, str], dict[str, float]] = {
        (b["symbol"], b["date"]): b for b in data["bars"]}
    states: dict[tuple[str, str], dict[str, Any]] = {
        (s["symbol"], s["date"]): s for s in data.get("states", [])}
    meta = _instrument_meta(data.get("instruments", []))
    actions = data.get("actions", [])
    targets: dict[str, dict[str, float]] = plan.get("targets", {})
    lot_size = int(plan.get("lot_size", 100))
    vpp = float((plan.get("execution") or {}).get("volume_participation", 0.0) or 0.0) or None
    explicit: dict[str, list[dict[str, Any]]] = {}
    for order in plan.get("orders", []):
        explicit.setdefault(order["date"], []).append(order)

    cash = float(plan.get("initial_cash", 1_000_000.0))
    positions: dict[str, dict[str, float]] = {}
    last_close: dict[str, float] = {}       # 重估基准（镜像 Portfolio._last_close）
    pending_dividends: list[dict[str, Any]] = []
    pending_orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []

    tv0 = tv_prev = tv_peak = None
    realized_total = 0.0

    for date in dates:
        # ① T+1 解冻（当日买入次日可卖）
        for pos in positions.values():
            pos["available"] = pos["qty"]

        # ① 公司行动：ex（调份额/成本）与 pay（现金到账）两时点
        for act in actions:
            if act.get("ex_date") != date:
                continue
            sym = act["symbol"]
            pos = positions.get(sym)
            if pos is None or pos["qty"] <= 0:
                continue                     # 无持仓 → no-op（引擎同语义）
            qty_at_ex = _effective_dividend_qty(positions, sym, pending_dividends)
            before = {"qty": pos["qty"], "avg_cost": pos["avg_cost"]}
            cash_div = float(act.get("cash_div_per_share") or 0.0)
            stk_div = float(act.get("stk_div_per_share") or 0.0)
            pay_date = act.get("pay_date") or date     # 缺失回退 ex（E5）
            if cash_div:
                pos["avg_cost"] -= cash_div
            if stk_div:
                factor = 1.0 + stk_div
                pos["qty"] = round(pos["qty"] * factor)
                pos["available"] = round(pos["available"] * factor)
                pos["avg_cost"] = pos["avg_cost"] / factor
            if cash_div:
                pending_dividends.append({"symbol": sym, "pay_date": pay_date,
                                          "qty_at_ex": qty_at_ex,
                                          "cash_div": cash_div})
            events.append({
                "date": date, "type": "ex", "symbol": sym,
                "qty_at_ex": qty_at_ex, "cash_div": cash_div, "stk_div": stk_div,
                "qty_before": before["qty"], "avg_cost_before": before["avg_cost"],
                "qty_after": pos["qty"], "avg_cost_after": pos["avg_cost"],
                "nav_delta": -qty_at_ex * cash_div if cash_div else 0.0,
            })

        for div in [d for d in pending_dividends if d["pay_date"] == date]:
            amount = div["qty_at_ex"] * div["cash_div"]
            cash += amount
            events.append({
                "date": date, "type": "pay", "symbol": div["symbol"],
                "qty_at_ex": div["qty_at_ex"], "cash_div": div["cash_div"],
                "cash_delta": amount, "nav_delta": amount,
            })
        pending_dividends = [d for d in pending_dividends if d["pay_date"] != date]

        # ② 撮合昨日订单（T+1 开盘价，无滑点）
        # 拒单链顺序**镜像引擎** NextOpenHandler._check：
        #   SUSPENDED → LIMIT_UP/LIMIT_DOWN → LOT_SIZE（买）→ VOLUME_CAP（买，
        #   VPP 上限<一手）→ T+N（卖）→ CASH（买，按 VPP 受限后数量预估）
        for order in pending_orders:
            sym, side, qty = order["symbol"], order["side"], order["qty"]
            bar = bars.get((sym, date))
            state = states.get((sym, date), {})
            info = meta.get(sym, {})
            lot = int(order.get("lot_size") or info.get("lot_size") or lot_size)
            t_plus = int(info.get("t_plus", 1))

            if bar is None or state.get("suspended"):
                rejections.append(_rejection(date, sym, side, qty, "suspended"))
                continue
            price = float(bar["open"])
            up, down = state.get("limit_up"), state.get("limit_down")
            if (side == "buy" and up is not None
                    and price >= float(up) * (1 - LIMIT_PRICE_TOL)):
                rejections.append(_rejection(date, sym, side, qty, "limit_up"))
                continue
            if (side == "sell" and down is not None
                    and price <= float(down) * (1 + LIMIT_PRICE_TOL)):
                rejections.append(_rejection(date, sym, side, qty, "limit_down"))
                continue
            if side == "buy" and qty % lot != 0:
                rejections.append(_rejection(date, sym, side, qty, "lot_size"))
                continue
            # VPP 受限数量（v0.5 V5-6 镜像 _capped_qty；G10）
            cap = min(qty, int(bar.get("vol", 0.0) * vpp)) if vpp else qty
            if side == "buy" and vpp:
                cap = cap // lot * lot
            if cap <= 0:
                rejections.append(_rejection(date, sym, side, qty, "volume_cap"))
                continue
            if side == "sell" and qty > positions.get(sym, {}).get("available", 0):
                rejections.append(_rejection(date, sym, side, qty, "t_plus_1"))
                continue
            fill_qty = cap
            fee = fees_of(sym, side, fill_qty, price, date)
            if side == "buy" and price * fill_qty + fee["total"] > cash + 1e-9:
                rejections.append(
                    _rejection(date, sym, side, qty, "insufficient_cash"))
                continue

            if side == "buy":
                pos = positions.setdefault(
                    sym, {"qty": 0, "available": 0, "avg_cost": 0.0,
                          "realized": 0.0})
                new_qty = pos["qty"] + fill_qty
                pos["avg_cost"] = ((pos["avg_cost"] * pos["qty"]
                                    + price * fill_qty + fee["total"]) / new_qty
                                   if new_qty else 0.0)
                pos["qty"] = new_qty
                if t_plus == 0:
                    pos["available"] += fill_qty  # T+0 品种当日可卖（E2 双维规则）
                cash -= price * fill_qty + fee["total"]
            else:
                pos = positions[sym]
                pnl = (price - pos["avg_cost"]) * fill_qty - fee["total"]
                pos["realized"] += pnl
                realized_total += pnl
                pos["qty"] -= fill_qty
                pos["available"] = max(0, pos["available"] - fill_qty)
                cash += price * fill_qty - fee["total"]
                if pos["qty"] <= 0:
                    del positions[sym]
            fills.append({"date": date, "symbol": sym, "side": side,
                          "qty": fill_qty, "price": price, "fee": fee})
        pending_orders = []

        # ⑤ 收盘重估 + ⑩ 快照（NAV 四式）
        # 估值基准**镜像** Portfolio：当日有 bar 则刷新 last_close；无 bar
        # （停牌 / 退市后）沿用上一已知收盘价，从未估值过才回退 avg_cost
        market_value = 0.0
        for sym, pos in positions.items():
            bar = bars.get((sym, date))
            if bar is not None:
                last_close[sym] = float(bar["close"])
            close = last_close.get(sym, pos["avg_cost"])
            pos["unrealized"] = (close - pos["avg_cost"]) * pos["qty"]
            market_value += pos["qty"] * close
        tv = cash + market_value
        if tv0 is None:
            tv0 = tv
        tv_peak = tv if tv_peak is None else max(tv_peak, tv)
        daily = 0.0 if tv_prev is None else tv / tv_prev - 1.0
        cumulative = tv / tv0 - 1.0 if tv0 else 0.0
        drawdown = tv / tv_peak - 1.0 if tv_peak else 0.0
        tv_prev = tv
        snapshots.append({
            "date": date, "cash": cash, "market_value": market_value,
            "total_value": tv, "daily_return": daily,
            "cumulative_return": cumulative, "drawdown": drawdown,
            "positions_qty": {s: p["qty"] for s, p in positions.items()},
            "avg_cost": {s: p["avg_cost"] for s, p in positions.items()},
        })

        # ⑥ 收盘决策：显式订单在前 + 目标权重差额法（卖先买后 + 整手向下取整）
        # 顺序**镜像引擎⑨** `drafts = explicit_orders + rebalancer(target)`；
        # 目标内无当日行情的标的**跳过**（不生成订单 → 不产生拒单，同 rebalancer）
        orders: list[dict[str, Any]] = [
            {"symbol": order["symbol"], "side": order["side"],
             "qty": int(order["qty"]),
             "lot_size": int(order.get("lot_size") or lot_size)}
            for order in explicit.get(date, [])
        ]
        target = targets.get(date)
        if target is not None:
            sells: list[dict[str, Any]] = []
            buys: list[dict[str, Any]] = []
            for sym in sorted({*target, *positions}):
                bar = bars.get((sym, date))
                if bar is None or float(bar["close"]) <= 0 or tv <= 0:
                    continue
                price = float(bar["close"])
                want_weight = float(target.get(sym, 0.0))
                want_qty = int(want_weight * tv // (price * lot_size)) * lot_size
                held = positions.get(sym, {}).get("qty", 0)
                diff = want_qty - held
                if diff > 0:
                    buys.append({"symbol": sym, "side": "buy", "qty": diff,
                                 "lot_size": lot_size})
                elif diff < 0:
                    # 不按 available 裁剪：可卖不足由 T+N 拒单链裁决（镜像引擎）
                    sells.append({"symbol": sym, "side": "sell", "qty": -diff,
                                  "lot_size": lot_size})
            orders.extend(sells + buys)          # 卖先买后（镜像 rebalancer 返回序）
        pending_orders = orders

    nav = [s["total_value"] for s in snapshots]
    metrics = _metrics(nav, fills)
    out: dict[str, Any] = {
        "contract_version": "golden.v1",
        "snapshots": snapshots,
        "fills": fills,
        "events": events,
        "metrics": metrics,
        "invariants": _invariants(snapshots, events, positions, realized_total),
    }
    # 拒单块**按需输出**：仅当案例含状态面板或显式订单（G2–G7 的断言对象）。
    # G1 案例不带这两项 → 输出逐字节不变（只增不改纪律：新增断言块不得
    # 改写既有案例的断言，否则会触发 GoldenAssertionDrift）。
    if states or explicit:
        out["rejections"] = rejections
        out["rejected_counts"] = _count_by_code(rejections)
    return out


def _count_by_code(rejections: list[dict[str, Any]]) -> dict[str, int]:
    """拒单按 code 计数（断言/证据用）。"""
    counts: dict[str, int] = {}
    for rej in rejections:
        counts[rej["code"]] = counts.get(rej["code"], 0) + 1
    return counts


def _metrics(nav: list[float], fills: list[dict]) -> dict[str, float]:
    """指标（独立实现：显式循环；口径同 04 §8.2.6/analytics.metrics）。"""
    if not nav:
        return {}
    rets = [0.0] + [nav[i] / nav[i - 1] - 1.0 for i in range(1, len(nav))]
    eff = rets[1:]
    n = len(eff)
    mean = sum(eff) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in eff) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    ann_factor = 252.0
    periods = max(1, len(nav) - 1)
    total_return = nav[-1] / nav[0] - 1.0 if nav[0] else 0.0
    ann_return = ((1 + total_return) ** (ann_factor / periods) - 1.0
                  if total_return > -1 else float("nan"))
    vol = sd * math.sqrt(ann_factor)
    sharpe = (mean / sd * math.sqrt(ann_factor)) if sd else float("nan")
    peak, mdd = 0.0, 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0 if peak else 0.0)
    calmar = ann_return / abs(mdd) if mdd else float("nan")
    wins = [r for r in eff if r > 0]
    losses = [r for r in eff if r < 0]
    win_rate = len(wins) / n if n else float("nan")
    plr = ((sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
           if wins and losses else float("nan"))
    fees = sum(f["fee"]["total"] for f in fills)
    turnover = sum(f["price"] * f["qty"] for f in fills)
    mean_nav = sum(nav) / len(nav)
    return {
        "final_nav": nav[-1], "total_return": total_return,
        "annualized_return": ann_return, "annualized_volatility": vol,
        "sharpe_ratio": sharpe, "max_drawdown": mdd, "calmar_ratio": calmar,
        "win_rate": win_rate, "profit_loss_ratio": plr,
        "n_fills": float(len(fills)), "total_fees": fees,
        "total_turnover": turnover,
        "turnover_annualized": turnover / mean_nav * (ann_factor / periods)
                               if mean_nav else float("nan"),
        "fee_ratio": fees / mean_nav if mean_nav else float("nan"),
        "n_trading_days": float(len(nav)),
    }


def _invariants(snapshots: list[dict], events: list[dict],
                positions: dict, realized_total: float) -> dict[str, Any]:
    """G1 连续性断言（09 §14.2）：送转单日 Δ=0；派息 ex→pay 窗口合并 Δ=0。"""
    nav_by_date = {s["date"]: s["total_value"] for s in snapshots}
    out: dict[str, Any] = {"event_days": [], "windows": []}
    for ev in events:
        if ev["type"] == "ex":
            out["event_days"].append({
                "date": ev["date"], "kind": "ex",
                "stk_div": ev["stk_div"], "cash_div": ev["cash_div"],
                "qty_before": ev["qty_before"], "qty_after": ev["qty_after"],
                "avg_cost_before": ev["avg_cost_before"],
                "avg_cost_after": ev["avg_cost_after"],
                "nav_delta": ev["nav_delta"],
            })
    # 派息窗口：ex 日 → pay 日的 NAV 合并变化应为 0（仅价格不变的理想口径下，
    # 实际因当日价格波动而不同——故断言对象为**事件贡献量**而非逐日 NAV 差）
    for ev in events:
        if ev["type"] == "ex" and ev["cash_div"]:
            pay = next((e for e in events if e["type"] == "pay"
                        and e["symbol"] == ev["symbol"]
                        and e["date"] >= ev["date"]), None)
            if pay is not None:
                out["windows"].append({
                    "symbol": ev["symbol"], "ex_date": ev["date"],
                    "pay_date": pay["date"], "qty_at_ex": pay["qty_at_ex"],
                    "ex_nav_delta": ev["nav_delta"], "pay_nav_delta": pay["nav_delta"],
                    "window_event_delta": ev["nav_delta"] + pay["nav_delta"],
                })
    out["final_cash"] = snapshots[-1]["cash"] if snapshots else 0.0
    out["realized_pnl_total"] = realized_total
    out["nav_last"] = nav_by_date
    return out


__all__ = ["compute_case", "fees_of"]
