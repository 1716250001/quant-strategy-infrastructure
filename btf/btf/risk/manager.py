# -*- coding: utf-8 -*-
"""RiskManager 协议（04 §8.3.4 预交易风控规则链；M1 任务 4.1 契约定稿）。

铁律 2（引擎四模块独立，03 §7.3）：本模块不 import portfolio——组合
依赖面以 PortfolioRiskView 协议声明（handler.py PortfolioView 同范式；
04 文档签名中的 Portfolio 类型按此偏差落地，装配期结构化注入）。

M1 范围：协议全集 + RuleChainManager（空链放行+警告，04 §8.5 扩展点 6
降级策略）。三条内置规则（risk/rules.py）与引擎 pre_check 接线归 M2
任务 5.2（本轮交付）。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Protocol

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.market import TradingState
from btf.domain.orders import Order

logger = logging.getLogger(__name__)


class PortfolioRiskView(Protocol):
    """风控所需组合视图（engine 注入 Portfolio；铁律 2 解耦）。

    结构与 strategy.context.PortfolioProbe 一致（cash/positions/
    total_value）+ close_of 盯市价（M2 5.2 权重/耗现预估所需），
    本模块局部声明以保持引擎四模块独立。
    """

    @property
    def cash(self) -> float: ...
    @property
    def positions(self) -> Mapping[str, Any]: ...
    def total_value(self) -> float: ...
    def close_of(self, symbol: str) -> float | None: ...


class VerdictKind(Enum):
    """裁决三态（04 §8.3.4：ALLOW | REDUCE_TO(qty) | REJECT(reason)）。"""

    ALLOW = "allow"
    REDUCE = "reduce"
    REJECT = "reject"


@dataclass(frozen=True)
class RiskVerdict:
    """规则裁决（不可变值对象）。"""

    kind: VerdictKind
    qty: int | None = None            # REDUCE：缩量后目标数量
    reason_code: str | None = None    # REJECT：机器可读原因

    @classmethod
    def allow(cls) -> RiskVerdict:
        return cls(VerdictKind.ALLOW)

    @classmethod
    def reduce_to(cls, qty: int) -> RiskVerdict:
        return cls(VerdictKind.REDUCE, qty=qty)

    @classmethod
    def reject(cls, reason_code: str) -> RiskVerdict:
        return cls(VerdictKind.REJECT, reason_code=reason_code)


class RiskRule(Protocol):
    """风控规则插件（NautilusTrader RiskEngine 思想）：预检订单 → 裁决。"""

    def check(self, order: Order, portfolio: PortfolioRiskView,
              states: Mapping[str, TradingState]) -> RiskVerdict: ...


class RiskManager(Protocol):
    """规则链编排（有序规则列表；首个非 ALLOW 裁决生效，04 §8.3.4）。"""

    rules: Sequence[RiskRule]

    def pre_check(self, orders: Sequence[Order], portfolio: PortfolioRiskView,
                  states: Mapping[str, TradingState]) -> list[Order]: ...


class RuleChainManager:
    """有序规则链实现（M2 5.2 完整版；M1 曾为空链放行占位）。

    语义：逐订单顺序过链，首个 REDUCE/REJECT 生效——
    REJECT → 剔除（batch_rejections 记录，引擎转 Rejection——06 §10.6
    否决进 Rejection code=risk_rejected）；REDUCE_TO(qty) → 订阅缩量
    （qty≥原量保持原单，qty≤0 等价剔除）。与 ExecutionHandler 拒单互补：
    风控在订单进入撮合前（04 §8.3.4），撮合拒单在执行时点。

    批次钩子：pre_check 开头对每条规则调 on_batch_start(portfolio)
    （可选；CashCheckRule 用以重置批次内可用现金额度）。
    """

    contract_version = CONTRACT_VERSION

    def __init__(self, rules: Sequence[RiskRule] | None = None):
        self.rules: list[RiskRule] = list(rules or [])
        self.batch_rejections: list[tuple[Order, str]] = []
        if not self.rules:
            logger.warning("风控规则链为空：全部订单放行（04 §8.5 扩展点 6 降级策略）")

    def pre_check(self, orders: Sequence[Order], portfolio: PortfolioRiskView,
                  states: Mapping[str, TradingState]) -> list[Order]:
        for rule in self.rules:
            hook = getattr(rule, "on_batch_start", None)
            if hook is not None:
                hook(portfolio)
        self.batch_rejections = []
        out: list[Order] = []
        for order in orders:
            verdict = RiskVerdict.allow()
            for rule in self.rules:
                verdict = rule.check(order, portfolio, states)
                if verdict.kind is not VerdictKind.ALLOW:
                    break                     # 首个非 ALLOW 生效
            if verdict.kind is VerdictKind.REJECT:
                self.batch_rejections.append(
                    (order, verdict.reason_code or "risk:unknown"))
                continue
            if (verdict.kind is VerdictKind.REDUCE
                    and verdict.qty is not None and verdict.qty < order.qty):
                if verdict.qty <= 0:
                    self.batch_rejections.append(
                        (order, verdict.reason_code or "risk:reduced_to_zero"))
                    continue
                order = replace(order, qty=verdict.qty)
            out.append(order)
        return out


__all__ = [
    "PortfolioRiskView",
    "RiskManager",
    "RiskRule",
    "RiskVerdict",
    "RuleChainManager",
    "VerdictKind",
]
