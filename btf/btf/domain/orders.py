# -*- coding: utf-8 -*-
"""订单域值对象：OrderType / OrderSide / Order / Fill / Fee / Rejection（04 §8.2.5/8.3.3）。

零第三方依赖（03 §7.3 铁律 3）。订单与成交分离（事件溯源纪律）：
Order 是不可变指令，Fill 是成交回报——引擎内核永不隐式修改订单状态，
状态流转只经事件（OrderSubmittedEvent / OrderRejectedEvent / FillEvent）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from btf.domain.types import TradingDate


class OrderType(Enum):
    """订单类型（MVP：MARKET/LIMIT；↗ v0.5 STOP/TWP/VWAP）。"""

    MARKET = "market"      # 次日开盘 or 当日收盘撮合（FillTiming 配置）
    LIMIT = "limit"        # 触价即成简化


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class RejectCode(Enum):
    """拒单机器可读 code（04 §8.3.3 拒单规则 + 18 号计划 2.4 六 code 全集）。

    顺序即检查顺序（撮合时点逐单判定）：
    SUSPENDED → LIMIT_UP / LIMIT_DOWN → LOT_SIZE → VOLUME_CAP →
    T_PLUS_1 → INSUFFICIENT_CASH
    """

    SUSPENDED = "suspended"            # 停牌/退市/当日无行情 → 拒单
    LIMIT_UP = "limit_up"              # 涨停买入拒（买不进）
    LIMIT_DOWN = "limit_down"          # 跌停卖出拒（卖不出）
    LOT_SIZE = "lot_size"              # 非整手倍数 → 拒单（引擎强制取整纪律）
    VOLUME_CAP = "volume_cap"          # VPP 撮合量上限 < 一手（v0.5，G10）
    T_PLUS_1 = "t_plus_1"              # T+N 可卖数量不足（当日买入部分不可卖）
    INSUFFICIENT_CASH = "insufficient_cash"  # 现金不足（含费用预估）
    RISK_REJECTED = "risk_rejected"    # 预交易风控否决（06 §10.6；message 含规则名）


@dataclass(frozen=True)
class Order:
    """订单（不可变指令）。订单状态机见 17 号文档；本类型不含可变状态。

    order_id：引擎内唯一，确定性生成（run 内自增 O00000001…，回放一致）。
    Rebalancer 产出草稿（order_id=""）→ 引擎⑨入队时统一赋号（06 §10.1）。
    """

    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: int                       # 委托数量（股；引擎强制 lot_size 取整，余数拒绝）
    limit_price: float | None
    created_at: TradingDate        # 策略下单日（T 日收盘决策 → T+1 执行为默认时序）
    tif: str = "day"               # Time-In-Force：MVP 仅当日有效
    tag: str | None = None         # 策略标签（归因分析用）


@dataclass(frozen=True)
class Fee:
    """费用分项（元）。成本模型插件产出；字段为契约（可视化/归因依赖）。

    PoC-2 提供零费用与单一现行费率两个简化模型；
    三段过户费/两段印花税的日期分段 FeeSchedule 属 M2 任务 5.1。
    """

    commission: float              # 佣金
    stamp_duty: float              # 印花税（仅卖出）
    transfer_fee: float            # 过户费
    total: float

    @classmethod
    def zero(cls) -> Fee:
        return cls(commission=0.0, stamp_duty=0.0, transfer_fee=0.0, total=0.0)


@dataclass(frozen=True)
class Fill:
    """成交回报（一次成交）。bar 级撮合下每日至多 1 fill/订单。"""

    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    qty: int
    price: float                   # 实际成交价（含滑点后）
    fee: Fee                       # 费用明细（分项，成本归因用）
    fill_date: TradingDate
    fill_timing: str               # "open"|"close"（撮合基准，报告披露用）


@dataclass(frozen=True)
class Trade:
    """交易（开平仓视角的成交聚合，04 §8.2.5；分析层与落盘 trades 契约）。

    FIFO 配对产出（btf/analytics/trades.py）：一次开仓可被多次平仓分摊，
    未平仓部分 close_fill=None 且 pnl=0.0（未实现，不进已实现口径——
    总收益一律 NAV 口径，见 04 §8.2.6 N5-1 加法禁区）。

    pnl 口径：平仓收入 − 开仓成本（含双边费用按配对数量比例摊入）。
    """

    trade_id: str
    symbol: str
    open_fill: Fill | None
    close_fill: Fill | None
    qty: int
    pnl: float
    holding_days: int
    tag: str | None = None


@dataclass(frozen=True)
class Rejection:
    """拒单（机器可读 code + 人类可读说明；09 §14.2 拒单统计的数据源）。"""

    code: RejectCode
    message: str = ""


__all__ = [
    "Fee",
    "Fill",
    "Order",
    "OrderSide",
    "OrderType",
    "RejectCode",
    "Rejection",
    "Trade",
]
