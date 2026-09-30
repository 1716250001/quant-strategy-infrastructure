# -*- coding: utf-8 -*-
"""成本与滑点模型（04 §8.3.3 协议；M2 任务 5.1 FeeSchedule 分段费率）。

公式对照（纪律 5——不写代码先写公式对照，04 §8.2.4 / §8.3.3 / 03 §7.6 H3 / 09 §14.2）：

    Fee 分项（元，四舍五入至分）：
        commission = max(turnover × commission_rate, commission_min)   [双向]
        stamp_duty = turnover × seg.stamp_duty_sell                    [仅卖出]
        transfer   = turnover × seg.transfer_fee_rate                  [basis=amount]
                   = qty × PAR_VALUE × seg.transfer_fee_rate           [basis=par]
                     （PAR_VALUE=1 元/股——A 股面值简化，紫金矿业 0.1 元例外披露）
        total = commission + stamp_duty + transfer

    费率分段（A_SHARE_SEGMENTS，财政部/中登文件口径，04 §8.2.4 注释冻结值）：
        | effective_from | stamp_duty_sell | transfer        | scope    | basis  |
        |----------------|-----------------|-----------------|----------|--------|
        | （无限早）      | 0.001           | 0.0006 元/股    | 仅沪市   | 面额   |
        | 2015-08-01     | 0.001           | 0.00002 (0.002%)| 沪深双向 | 成交额 |
        | 2022-04-29     | 0.001           | 0.00001 (0.001%)| 沪深双向 | 成交额 |
        | 2023-08-28     | 0.0005          | 0.00001         | 沪深双向 | 成交额 |
      - 印花税两段（H3）：2023-08-28 起减半 0.05%（此前 0.1%）；2008 年前更细
        历史（0.2%/0.3%/双向等）不建模——段 1 统一 0.001，docstring 披露近似；
      - 过户费三段（N2）："2015-08 前仅沪市"经段结构 transfer_scope 表达
        （适用标的集合随时间变化，非纯费率数值分段）；沪市老口径按成交面额
        0.6‰（面值 1 元/股 → 0.0006 元/股）；
      - 段查找：effective_from ≤ date 的最晚段（None=无限早）；date 早于全部
        段 → 显式报错（不静默回退错口径段）。

    滑点（纯函数——确定性纪律，无随机源）：
        FixedSlippage:  fill_price = ref ± amount        [买 + / 卖 −]
        PctSlippage:    fill_price = ref × (1 ± pct)     [买 + / 卖 −]

协议签名（04 §8.3.3 原文，M1 简化偏差本轮对齐——fees 含 date 分段选段所需；
apply(order, ref_price, state, bar) 为 v0.5 VPP 成交量约束预留）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.market import Bar, TradingState
from btf.domain.orders import Fee, Order, OrderSide
from btf.domain.types import TradingDate

#: A 股面值简化（元/股；过户费面额口径基数）
PAR_VALUE = 1.0


class CostModel(Protocol):
    """费用协议（04 §8.3.3）：fees(symbol, side, qty, price, date) → Fee（元）。纯函数。"""

    def fees(self, symbol: str, side: OrderSide, qty: int, price: float,
             date: TradingDate) -> Fee: ...


class SlippageModel(Protocol):
    """滑点协议（04 §8.3.3）：撮合基准价 → 实际成交价。纯函数（无随机——确定性纪律）。"""

    def apply(self, order: Order, ref_price: float,
              state: TradingState, bar: Bar | None) -> float: ...


@dataclass(frozen=True)
class FeeSegment:
    """费率分段（04 §8.2.4 FeeSchedule 的日期区间生效版本；N2 含适用集合分段）。

    过户费"仅沪市"时期（2015-08 前）= scope="sh_only"——适用标的集合随
    时间变化，不是纯费率数值分段（transfer_fee 适用市场随时间变化）。
    """

    effective_from: TradingDate | None      # None = 无限早（首段）
    stamp_duty_sell: float                  # 印花税（仅卖出）
    transfer_fee_rate: float                # 过户费率（basis 口径）
    transfer_scope: str = "both"            # "sh_only" | "both"
    transfer_basis: str = "amount"          # "amount" 成交额 | "par" 面额


#: 内置 A 股分段费率表（变更点合并切分：过户费三段 × 印花税两段 → 4 段）
A_SHARE_SEGMENTS: tuple[FeeSegment, ...] = (
    FeeSegment(                               # 2015-08-01 前：仅沪市按面额 0.6‰
        effective_from=None,
        stamp_duty_sell=0.001,
        transfer_fee_rate=0.0006,             # 元/股（面额 0.6‰ × 1 元面值）
        transfer_scope="sh_only",
        transfer_basis="par",
    ),
    FeeSegment(                               # 2015-08-01：沪深双向 0.002%（成交额）
        effective_from=TradingDate.from_ymd("20150801"),
        stamp_duty_sell=0.001,
        transfer_fee_rate=0.00002,
    ),
    FeeSegment(                               # 2022-04-29：过户费减半 0.001%
        effective_from=TradingDate.from_ymd("20220429"),
        stamp_duty_sell=0.001,
        transfer_fee_rate=0.00001,
    ),
    FeeSegment(                               # 2023-08-28：印花税减半 0.05%（H3）
        effective_from=TradingDate.from_ymd("20230828"),
        stamp_duty_sell=0.0005,
        transfer_fee_rate=0.00001,
    ),
)


def _segment_at(segments: tuple[FeeSegment, ...],
                date: TradingDate) -> FeeSegment:
    """date 生效段（effective_from ≤ date 的最晚段；早于全部段 → 显式报错）。"""
    hit: FeeSegment | None = None
    for seg in segments:
        if seg.effective_from is None or seg.effective_from <= date:
            hit = seg
    if hit is None:
        raise ValueError(
            f"费率表未覆盖 {date.to_ymd()}（早于首段 "
            f"{segments[0].effective_from.to_ymd() if segments[0].effective_from else '?'}"
            f"——自定义段表须以 None 首段覆盖全程）")
    return hit


@dataclass(frozen=True)
class ZeroCostModel:
    """零费用基线（守恒式与双跑确定性测试默认；正式回测勿用——成本系统性低估）。"""

    contract_version = CONTRACT_VERSION

    def fees(self, symbol: str, side: OrderSide, qty: int, price: float,
             date: TradingDate) -> Fee:
        return Fee.zero()


@dataclass(frozen=True)
class AShareTieredFeeModel:
    """A 股分段费率模型（04 §8.5 扩展点 3 MVP 默认实现；注册名 tiered_v1）。

    佣金不随日期分段（券商费率，模型级参数可配置覆盖）；印花税/过户费按
    A_SHARE_SEGMENTS（或自定义 segments）日期分段。近似口径披露：2008 前
    印花税更细历史不建模；A 股面值统一 1 元/股（紫金矿业 0.1 元例外）。
    """

    contract_version: ClassVar[str] = CONTRACT_VERSION

    commission_rate: float = 2.5e-4          # 万 2.5（券商默认口径）
    commission_min: float = 5.0              # 单笔最低佣金（元，双向）
    segments: tuple[FeeSegment, ...] = A_SHARE_SEGMENTS

    def fees(self, symbol: str, side: OrderSide, qty: int, price: float,
             date: TradingDate) -> Fee:
        seg = _segment_at(self.segments, date)
        turnover = qty * price
        commission = max(turnover * self.commission_rate, self.commission_min)
        stamp = turnover * seg.stamp_duty_sell if side is OrderSide.SELL else 0.0
        if seg.transfer_scope == "sh_only" and not symbol.endswith(".SH"):
            transfer = 0.0                     # 2015-08 前深市不收过户费
        elif seg.transfer_basis == "par":
            transfer = qty * PAR_VALUE * seg.transfer_fee_rate
        else:
            transfer = turnover * seg.transfer_fee_rate
        return Fee(commission=round(commission, 2), stamp_duty=round(stamp, 2),
                   transfer_fee=round(transfer, 2),
                   total=round(commission + stamp + transfer, 2))


@dataclass(frozen=True)
class FlatRateCostModel:
    """单一现行费率近似（PoC 遗留；历史分段回测请用 AShareTieredFeeModel）。

    口径（现行常数，不随日期分段）：commission 万 2.5 最低 5 元；stamp 卖出
    0.5‰；transfer 双向 0.01‰。
    """

    contract_version: ClassVar[str] = CONTRACT_VERSION

    commission_rate: float = 2.5e-4
    min_commission: float = 5.0
    stamp_duty_rate: float = 5e-4      # 仅卖出
    transfer_rate: float = 1e-5

    def fees(self, symbol: str, side: OrderSide, qty: int, price: float,
             date: TradingDate) -> Fee:  # date 未用（现行常数口径）
        turnover = qty * price
        commission = max(turnover * self.commission_rate, self.min_commission)
        stamp = turnover * self.stamp_duty_rate if side is OrderSide.SELL else 0.0
        transfer = turnover * self.transfer_rate
        return Fee(commission=round(commission, 2), stamp_duty=round(stamp, 2),
                   transfer_fee=round(transfer, 2),
                   total=round(commission + stamp + transfer, 2))


# ── 滑点（扩展点 4：FixedSlippage / PctSlippage；未配置→NoSlippage + 报告标注）──


@dataclass(frozen=True)
class NoSlippage:
    """无滑点：基准价撮合（回测基线；报告显著标注）。"""

    contract_version = CONTRACT_VERSION

    def apply(self, order: Order, ref_price: float,
              state: TradingState, bar: Bar | None) -> float:
        return ref_price


@dataclass(frozen=True)
class FixedSlippage:
    """固定滑点（元）：买 +amount / 卖 −amount。"""

    contract_version = CONTRACT_VERSION

    amount: float = 0.01                     # 元（默认一档报价）

    def apply(self, order: Order, ref_price: float,
              state: TradingState, bar: Bar | None) -> float:
        return (ref_price + self.amount if order.side is OrderSide.BUY
                else ref_price - self.amount)


@dataclass(frozen=True)
class PctSlippage:
    """百分比滑点：买 ×(1+pct) / 卖 ×(1−pct)。pct=0.001 即 10bp。"""

    contract_version = CONTRACT_VERSION

    pct: float = 0.001                       # 0.1%

    def apply(self, order: Order, ref_price: float,
              state: TradingState, bar: Bar | None) -> float:
        return (ref_price * (1 + self.pct) if order.side is OrderSide.BUY
                else ref_price * (1 - self.pct))


__all__ = [
    "A_SHARE_SEGMENTS",
    "AShareTieredFeeModel",
    "CostModel",
    "FeeSegment",
    "FixedSlippage",
    "FlatRateCostModel",
    "NoSlippage",
    "PctSlippage",
    "SlippageModel",
    "ZeroCostModel",
]
