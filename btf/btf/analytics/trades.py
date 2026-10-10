# -*- coding: utf-8 -*-
"""成交 → 交易聚合（FIFO 开平配对，04 §8.2.5 Trade；M2 任务 5.5 支撑）。

RunStore 的 trades 产物与 Analyzer 的 ``trades`` 参数同源：引擎产出的是
Fill 流水（成交视角），分析需要开平成对（交易视角）——配对规则必须确定
（同输入 → 同输出，R1 复现前提）。

口径：
    - FIFO：先开的仓先被平（与 A 股成本移动的加权平均法在盈亏总额上等价，
      仅分摊到单笔的金额不同——总额守恒：Σ trade.pnl = realized_pnl 合计）；
    - 费用按配对数量比例摊入（开仓费 + 平仓费各摊 qty/原成交数量）；
    - 未平仓（收盘仍持有）：close_fill=None，pnl=0.0（未实现口径，浮盈
      不进已实现；总收益一律 NAV 口径——04 §8.2.6 N5-1 加法禁区）；
    - 排序键 (fill_date, order_id, fill_id) 保证与引擎入账顺序无关时亦确定。

**R61′（N63-a/N63-b，2026-10-10；19 号 §113.2/§114.2）——折算感知**：
本函数此前**只消费 Fill 流水、对公司行动完全无感知** ⇒ 份额折算后队列仍记
折算前数量（N63-a）、`_lot_pnl` 仍用折算前成交价作成本基准（N63-b）⇒ 配对挂错
批次、未平仓虚高（510580 实测 141,009+27,100=168,109）、`win_rate_trades`
系统性虚高（亏损记成盈利）。今按 `consolidations` 参数在遍历 fill 前应用
**市值守恒变换**：**剩余数量 ×R**（R45′ 碎份口径：向下取整丢弃；`int(...) ≤ 0`
走 R21′ 保底＝留 1 份、成本价不变——与 `portfolio.apply_ex_date` **同源**）
且 **成本价 ÷R**；费用摊销比例分母同步用**调整后的 lot 原始量**。
**只对 ex_date 之前建立的 lot 生效**（同日及之后买入不受影响——§114.4 R-3/R-4）。
"""
from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from btf.domain.action import ratio_or_default
from btf.domain.orders import Fill, OrderSide, Trade
from btf.domain.types import TradingDate, parse_trading_date

logger = logging.getLogger(__name__)

__all__ = ["Consolidation", "normalize_consolidations", "pair_fills_to_trades"]

#: 折算事件（规范形态）：`(symbol, ex_date, ratio)`
Consolidation = tuple[str, TradingDate, float]


def normalize_consolidations(
    events: Iterable[Any] | None,
) -> tuple[Consolidation, ...]:
    """公司行动事件 → **规范折算序列**（R61′；§114.2 第 1 条「读写同一入口」）。

    接受两种形态（同一入口）：
      - 元组 `(symbol, ex_date, ratio)`——`ex_date` 为 `TradingDate`／
        `'YYYYMMDD'`／`'YYYY-MM-DD'` 字符串；
      - 映射（portfolio `consolidations` 事件／`events.jsonl`
        `corporate_action` 负载）：键 `symbol`／`ex_date`／`ratio`。

    归一化（**禁崩、禁静默**，与 R54′ 同族）：
      ① `ratio` 经 `ratio_or_default`——`None`／不可转 ⇒ `1.0` ＋ WARN；
         `≤0` ⇒ `1.0` ＋ WARN（**视为无效值、不建模**——N59 裁决 (a)）；
      ② `ratio == 1.0` 事件剔除（无行动，`portfolio` 同门：`cons != 1.0`）；
      ③ 形态不合法的事件**跳过并 WARN**（缺键／不可解析日期）；
      ④ 输出按 `ex_date` **升序**（同日多事件保持输入序——确定性）。
    """
    if not events:
        return ()
    out: list[Consolidation] = []
    for raw in events:
        symbol = ex_date = None
        ratio_raw: Any = None
        if isinstance(raw, Mapping):
            symbol = raw.get("symbol")
            ex_date = raw.get("ex_date")
            ratio_raw = raw.get("ratio")
        elif isinstance(raw, (tuple, list)) and len(raw) >= 3:
            symbol, ex_date, ratio_raw = raw[0], raw[1], raw[2]
        else:
            logger.warning("折算事件形态不可识别（%r）⇒ 跳过（R61′；禁静默）", raw)
            continue
        try:
            ex = parse_trading_date(ex_date)
        except (TypeError, ValueError):
            logger.warning("折算事件 ex_date 不可解析（%r@%r）⇒ 跳过（R61′）",
                           ex_date, symbol)
            continue
        ratio = ratio_or_default(ratio_raw, context=f"trades:{symbol}:{ex.to_ymd()}",
                                 warn=logger.warning)
        if ratio == 1.0:
            continue                      # 无行动（含无效值归一化的结果）
        out.append((str(symbol), ex, ratio))
    out.sort(key=lambda item: (item[1], ))          # 稳定排序（同日保序）
    return tuple(out)


def _sort_key(fill: Fill) -> tuple:
    return (fill.fill_date.iso, fill.order_id, fill.fill_id)


def _apply_consolidation(lot: list, ratio: float) -> None:
    """队列内单个 lot 的**市值守恒变换**（R61′/N63-a；§114.2 第 2 条）。

    `lot = [open_fill, 剩余数量, 调整后成本价, 调整后原始量]`：
      - **剩余数量 × R**（`int(...)` 向下取整——R45′ 碎份口径，与
        `portfolio.apply_ex_date` 的 `raw_qty = int(qty_at_ex × cons)` 同源）；
      - **成本价 ÷ R**（市值守恒；多次折算**累积**非覆盖——R-2）；
      - 调整后原始量 × R（**费用摊销比例分母**——§114.2 第 3 条）；
      - `int(剩余 × R) ≤ 0` ⇒ **R21′ 保底**：留 1 份、成本价**不变**、
        原始量按 1 计（组合层同源语义——不清零持仓）。
    `Fill` 为 frozen dataclass ⇒ **只改 lot 内的调整量，不原地改 Fill**。
    """
    raw_qty = int(lot[1] * ratio)
    if raw_qty <= 0:
        lot[1] = 1
        lot[3] = 1.0
        return
    lot[1] = raw_qty
    lot[2] = lot[2] / ratio
    lot[3] = lot[3] * ratio


def _lot_pnl(open_fill: Fill, close_fill: Fill, qty: int, *,
             cost_price: float | None = None,
             orig_qty: float | None = None) -> float:
    """配对盈亏：平仓收入 − 开仓成本（双边费用按数量比例摊入）。

    **R61′/N63-b**：`cost_price`＝**折算调整后**的成本基准（缺省＝原成交价，
    向后兼容）；`orig_qty`＝**调整后**的 lot 原始量（费用摊销分母——§114.2
    第 3 条：不得再用折算前的 `open_fill.qty`）。
    """
    basis = open_fill.price if cost_price is None else cost_price
    denom = open_fill.qty if orig_qty is None else orig_qty
    open_fee = open_fill.fee.total * (qty / denom) if denom else 0.0
    close_fee = close_fill.fee.total * (qty / close_fill.qty) if close_fill.qty else 0.0
    cost = basis * qty + open_fee
    proceeds = close_fill.price * qty - close_fee
    return proceeds - cost


def pair_fills_to_trades(
    fills: Sequence[Fill],
    *,
    consolidations: Sequence[Any] | None = None,
    sort: bool = True,
    tag_of: Callable[[Fill], str | None] | None = None,
) -> list[Trade]:
    """Fill 流水 → Trade 列表（FIFO；确定性）。

    卖出超出持仓的部分（理论上不会发生——撮合层 T_PLUS_1 兜底）忽略，
    不静默造数：剩余卖量丢弃并保留为 qty 不足的最后一笔（以实有可配数
    量配对）。

    **`consolidations`（R61′；19 号 §114.2）**：份额折算事件序列
    （`(symbol, ex_date, ratio)` 元组或 portfolio/事件日志映射——见
    `normalize_consolidations`）。时序语义：**ex_date 当日的成交先应用折算、
    再配对**（当日卖出按折算后份额/价格；当日买入的 lot **不参与**该次折算），
    末次成交之后的折算在收尾时一并应用——保证「Σ 未平仓 qty == 末日持仓」。
    """
    ordered = sorted(fills, key=_sort_key) if sort else list(fills)
    cons = normalize_consolidations(consolidations)
    lots: dict[str, deque[list]] = {}       # symbol → [[open_fill, 剩余, 价, 原始量], …]
    out: list[Trade] = []
    seq = 0
    ci = 0

    def _emit(open_fill: Fill | None, close_fill: Fill | None, qty: int,
              pnl: float, holding: int) -> None:
        nonlocal seq
        seq += 1
        out.append(Trade(
            trade_id=f"T{seq:06d}", symbol=(open_fill or close_fill).symbol,
            open_fill=open_fill, close_fill=close_fill, qty=qty, pnl=pnl,
            holding_days=holding,
            tag=tag_of(open_fill) if tag_of and open_fill is not None else None,
        ))

    def _apply_pending(upto: TradingDate | None) -> None:
        """应用 `ex_date <= upto`（`None`＝全部剩余）的折算到队列（R61′）。"""
        nonlocal ci
        while ci < len(cons) and (upto is None or cons[ci][1] <= upto):
            symbol, _ex, ratio = cons[ci]
            queue = lots.get(symbol)
            if queue:
                for lot in queue:
                    _apply_consolidation(lot, ratio)
            ci += 1

    for fill in ordered:
        _apply_pending(fill.fill_date)      # 折算发生时点先于当日配对（同日买入不受影响）
        if fill.side is OrderSide.BUY:
            lots.setdefault(fill.symbol, deque()).append(
                [fill, fill.qty, fill.price, float(fill.qty)])
            continue
        need = fill.qty
        queue = lots.get(fill.symbol)
        while need > 0 and queue:
            lot = queue[0]
            qty = min(need, lot[1])
            open_fill = lot[0]
            holding = (fill.fill_date.iso - open_fill.fill_date.iso).days
            _emit(open_fill, fill, qty,
                  _lot_pnl(open_fill, fill, qty, cost_price=lot[2],
                           orig_qty=lot[3]), holding)
            lot[1] -= qty
            need -= qty
            if lot[1] <= 0:
                queue.popleft()

    _apply_pending(None)                    # 末次成交之后的折算（R-2 累积/收尾口径）

    # 未平仓部分（收盘仍持有）：pnl=0（未实现）
    for symbol in sorted(lots):
        for open_fill, remaining, _price, _orig in lots[symbol]:
            if remaining > 0:
                _emit(open_fill, None, remaining, 0.0, 0)
    return out
