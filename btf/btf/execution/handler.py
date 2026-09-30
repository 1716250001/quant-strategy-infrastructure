# -*- coding: utf-8 -*-
"""ExecutionHandler：NextOpen 撮合 + 拒单规则（04 §8.3.3；18 号计划 2.4）。

NextOpen 语义：T-1 收盘决策订单 → T 开盘价撮合（默认撮合窗口）。
拒单 code 检查顺序（04 §8.3.3 + 计划 2.4 + v0.5 VPP，顺序即语义，不可重排）：
    ① SUSPENDED           停牌/退市/当日无行情（状态面板或截面缺席）
    ② LIMIT_UP            涨停买入拒：open ≥ limit_up×(1−tol)
    ③ LIMIT_DOWN          跌停卖出拒：open ≤ limit_down×(1+tol)
    ④ LOT_SIZE            买单非整手倍数（卖出允许零股一次性了结，A股规则）
    ⑤ VOLUME_CAP          VPP 撮合量上限 < 一手（v0.5；bar 成交量 × 参与率
                          不足一手 → 完全拒单；够一手 → **部分成交**，剩余
                          量当日取消——bar 级无盘中信息，不排队不追单）
    ⑥ T_PLUS_1            卖出可卖数量不足（available_qty，含 T+0 品种判定）
    ⑦ INSUFFICIENT_CASH   买入现金不足（含费用预估；逐单扣减，卖先买后
                          由 Rebalancer 排序保证——同日卖出回笼资金可供
                          后续买单，顺序处理天然实现）
涨跌停容差 1e-4（浮点边界；计划 2.4 验收）。

VPP（Volume Participation，v0.5 V5-6；14 号 §24「成交量 × 5% 参与率上限」）：
    volume_participation=None → v0.2 语义（全成或全拒）不变；
    设为 (0,1] → 成交量 = min(委托量, bar.volume × rate)；买入按整手向下
    取整（cap < lot → VOLUME_CAP 拒单），卖出按整数股（零股了结允许）。
    部分成交的「未成交余量」**当日取消**（TIF=day），不跨日排队——假设
    显式披露（报告假设章节消费 handler.vpp_stats）。

铁律 2（引擎四模块不互相 import）：本模块不 import portfolio——
依赖面以 PortfolioView 协议声明（结构化类型，engine 组装时注入）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.market import Bar, TradingState
from btf.domain.orders import (
    Fill,
    Order,
    OrderSide,
    RejectCode,
    Rejection,
)
from btf.domain.types import AssetClass, Instrument, TradingDate, rule_for
from btf.execution.cost import CostModel, NoSlippage, SlippageModel, ZeroCostModel

#: 涨跌停判定浮点容差（计划 2.4 验收：1e-4）
LIMIT_PRICE_TOL = 1e-4


class PortfolioView(Protocol):
    """撮合所需组合视图（engine 注入 Portfolio；铁律 2 解耦）。"""

    @property
    def cash(self) -> float: ...
    def available_qty(self, symbol: str) -> int: ...
    def apply_fill(self, fill: Fill, *, t_plus: int = 1) -> None: ...


def _default_instrument(symbol: str) -> Instrument:
    """缺省按沪深主板股票（lot 100 / T+1）；正式映射由 engine 注入。"""
    return Instrument(symbol=symbol, asset_class=AssetClass.STOCK, board="main")


class NextOpenHandler:
    """次日开盘价撮合（bar 级：一订单一日至多一 fill）。

    顺序处理订单（不重排——排序语义归 Rebalancer「卖先买后」）；
    每笔成交即时入账（apply_fill），后续订单的现金/可卖检查因此反映
    同日先前的成交（INSUFFICIENT_CASH 逐单扣减语义）。
    """

    contract_version = CONTRACT_VERSION

    def __init__(
        self,
        cost_model: CostModel | None = None,
        slippage_model: SlippageModel | None = None,
        instruments: Mapping[str, Instrument] | None = None,
        fill_seq_start: int = 0,
        volume_participation: float | None = None,
    ):
        self._cost = cost_model or ZeroCostModel()
        self._slip = slippage_model or NoSlippage()
        self._instruments = dict(instruments or {})
        self._fill_seq = fill_seq_start
        if volume_participation is not None and not 0 < volume_participation <= 1:
            raise ValueError(
                f"volume_participation 须 ∈ (0,1]，得 {volume_participation!r}")
        self._vpp = volume_participation
        #: VPP 披露统计（报告假设章节消费）：部分成交笔数 / 未成交余量（股）
        self.vpp_stats = {"partial_fills": 0, "unfilled_qty": 0}

    # ── 协议实现（04 §8.3.3 execute 签名 + 计划 2.4 现金终检扩展）──
    def execute(
        self,
        orders: Sequence[Order],
        date: TradingDate,
        data: Mapping[str, Bar],
        states: Mapping[str, TradingState],
        portfolio: PortfolioView,
    ) -> tuple[list[Fill], list[tuple[Order, Rejection]]]:
        fills: list[Fill] = []
        rejections: list[tuple[Order, Rejection]] = []
        for order in orders:
            rejection = self._check(order, date, data, states, portfolio)
            if rejection is not None:
                rejections.append((order, rejection))
                continue
            fill = self._fill(order, date, data, states)
            portfolio.apply_fill(fill, t_plus=self._t_plus(order.symbol))
            fills.append(fill)
        return fills, rejections

    # ── 拒单链（顺序即语义：SUSPENDED→LIMIT→LOT→T+1→CASH）──
    def _check(
        self,
        order: Order,
        date: TradingDate,
        data: Mapping[str, Bar],
        states: Mapping[str, TradingState],
        portfolio: PortfolioView,
    ) -> Rejection | None:
        bar = data.get(order.symbol)
        state = states.get(order.symbol)

        # ① 停牌/退市/无行情
        if bar is None or state is None or state.is_suspended or state.is_delisted:
            return Rejection(RejectCode.SUSPENDED, "停牌/退市或当日无行情")

        # ②③ 涨跌停（开盘价口径；状态面板缺失时不误拒——数据缺失≠可成交）
        if order.side is OrderSide.BUY and state.limit_up_price is not None:
            if bar.open >= state.limit_up_price * (1 - LIMIT_PRICE_TOL):
                return Rejection(RejectCode.LIMIT_UP, "开盘涨停，买入拒单")
        if order.side is OrderSide.SELL and state.limit_down_price is not None:
            if bar.open <= state.limit_down_price * (1 + LIMIT_PRICE_TOL):
                return Rejection(RejectCode.LIMIT_DOWN, "开盘跌停，卖出拒单")

        # ④ 整手（买入建仓强制；卖出允许零股一次性了结）
        if order.side is OrderSide.BUY:
            lot = self._lot_size(order.symbol)
            if order.qty % lot != 0:
                return Rejection(RejectCode.LOT_SIZE, f"非整手倍数（lot={lot}）")

        # ⑤ VPP 撮合量上限（v0.5）：cap < 一手（买）→ 完全拒单
        if self._vpp is not None:
            cap = self._capped_qty(order, bar)
            if cap <= 0:
                return Rejection(RejectCode.VOLUME_CAP, (
                    f"VPP 上限不足一手：vol={bar.volume:.0f}×"
                    f"{self._vpp:g}={bar.volume * self._vpp:.0f} < lot="
                    f"{self._lot_size(order.symbol)}"))

        # ⑥ T+N 可卖数量
        if order.side is OrderSide.SELL:
            available = portfolio.available_qty(order.symbol)
            if order.qty > available:
                return Rejection(
                    RejectCode.T_PLUS_1,
                    f"可卖不足：需 {order.qty} > 可用 {available}",
                )

        # ⑦ 现金（含费用预估，逐单扣减口径；VPP 下按**受限后数量**预估）
        if order.side is OrderSide.BUY:
            qty = self._capped_qty(order, bar) if self._vpp is not None else order.qty
            price = self._slip.apply(order, bar.open, state, bar)
            fee = self._cost.fees(order.symbol, OrderSide.BUY, qty,
                                  price, date)
            need = price * qty + fee.total
            if need > portfolio.cash + 1e-9:
                return Rejection(
                    RejectCode.INSUFFICIENT_CASH,
                    f"现金不足：需 {need:.2f} > 可用 {portfolio.cash:.2f}",
                )
        return None

    # ── VPP 受限数量（v0.5 V5-6；None 语义=不约束）──
    def _capped_qty(self, order: Order, bar: Bar) -> int:
        """委托量 → VPP 受限成交量：min(qty, vol×rate)；买按整手向下取整。"""
        cap = int(bar.volume * self._vpp)
        if order.side is OrderSide.BUY:
            lot = self._lot_size(order.symbol)
            cap = cap // lot * lot
        return min(order.qty, cap)

    # ── 成交构造 ──
    def _fill(self, order: Order, date: TradingDate,
              data: Mapping[str, Bar],
              states: Mapping[str, TradingState]) -> Fill:
        bar = data[order.symbol]
        state = states[order.symbol]
        qty = self._capped_qty(order, bar) if self._vpp is not None else order.qty
        if qty < order.qty:                     # 部分成交：余量当日取消（披露）
            self.vpp_stats["partial_fills"] += 1
            self.vpp_stats["unfilled_qty"] += order.qty - qty
        price = self._slip.apply(order, bar.open, state, bar)
        fee = self._cost.fees(order.symbol, order.side, qty,
                              price, date)
        self._fill_seq += 1
        return Fill(
            fill_id=f"F{self._fill_seq:08d}",
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            qty=qty,
            price=price,
            fee=fee,
            fill_date=date,
            fill_timing="open",
        )

    # ── 品种规则 ──
    def _instrument(self, symbol: str) -> Instrument:
        inst = self._instruments.get(symbol)
        return inst if inst is not None else _default_instrument(symbol)

    def _lot_size(self, symbol: str) -> int:
        return self._instrument(symbol).lot_size

    def _t_plus(self, symbol: str) -> int:
        return rule_for(self._instrument(symbol)).t_plus


__all__ = ["LIMIT_PRICE_TOL", "NextOpenHandler", "PortfolioView"]
