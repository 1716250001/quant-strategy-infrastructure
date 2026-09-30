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
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence

from btf.domain.orders import Fill, OrderSide, Trade

__all__ = ["pair_fills_to_trades"]


def _sort_key(fill: Fill) -> tuple:
    return (fill.fill_date.iso, fill.order_id, fill.fill_id)


def _lot_pnl(open_fill: Fill, close_fill: Fill, qty: int) -> float:
    """配对盈亏：平仓收入 − 开仓成本（双边费用按数量比例摊入）。"""
    open_fee = open_fill.fee.total * (qty / open_fill.qty) if open_fill.qty else 0.0
    close_fee = close_fill.fee.total * (qty / close_fill.qty) if close_fill.qty else 0.0
    cost = open_fill.price * qty + open_fee
    proceeds = close_fill.price * qty - close_fee
    return proceeds - cost


def pair_fills_to_trades(
    fills: Sequence[Fill],
    *,
    sort: bool = True,
    tag_of: Callable[[Fill], str | None] | None = None,
) -> list[Trade]:
    """Fill 流水 → Trade 列表（FIFO；确定性）。

    卖出超出持仓的部分（理论上不会发生——撮合层 T_PLUS_1 兜底）忽略，
    不静默造数：剩余卖量丢弃并保留为 qty 不足的最后一笔（以实有可配数
    量配对）。
    """
    ordered = sorted(fills, key=_sort_key) if sort else list(fills)
    lots: dict[str, deque[list]] = {}       # symbol → [[open_fill, 剩余数量], …]
    out: list[Trade] = []
    seq = 0

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

    for fill in ordered:
        if fill.side is OrderSide.BUY:
            lots.setdefault(fill.symbol, deque()).append([fill, fill.qty])
            continue
        need = fill.qty
        queue = lots.get(fill.symbol)
        while need > 0 and queue:
            lot = queue[0]
            qty = min(need, lot[1])
            open_fill = lot[0]
            holding = (fill.fill_date.iso - open_fill.fill_date.iso).days
            _emit(open_fill, fill, qty, _lot_pnl(open_fill, fill, qty), holding)
            lot[1] -= qty
            need -= qty
            if lot[1] <= 0:
                queue.popleft()

    # 未平仓部分（收盘仍持有）：pnl=0（未实现）
    for symbol in sorted(lots):
        for open_fill, remaining in lots[symbol]:
            if remaining > 0:
                _emit(open_fill, None, remaining, 0.0, 0)
    return out
