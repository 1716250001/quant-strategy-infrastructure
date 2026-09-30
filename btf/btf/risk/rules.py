# -*- coding: utf-8 -*-
"""三条内置风控规则（M2 任务 5.2；04 §8.3.4 内置规则全集）。

语义对照（04 §8.3.4 + 06 §10.6）：
    - 与 ExecutionHandler 拒单规则互补：风控在订单进入撮合前（决策时点
      T 收盘），撮合拒单在执行时点（T+1 开盘）——同 code 语义双时点检查；
    - 否决进 Rejection（code=risk_rejected，message 含规则名——06 §10.6）；
    - 缩量（REDUCE_TO）保持整手（lot_size 向下取整——非整手会被撮合 LOT
      拒单反而劣化，缩量意义=可执行的最大目标）；
    - 数据缺失（盯市价无）→ 降级放行（单指标异常不中断，04 §8.5；撮合层
      兜底拒单）。

规则参数（配置声明制：risk.rules[{name, params}]，registry 名字表）：
    max_weight:    max_weight=0.10, lot_size=100
    cash_check:    fee_buffer=0.001, lot_size=100
    tradability:   reject_st=False
"""
from __future__ import annotations

import logging
from collections.abc import Mapping

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.market import TradingState
from btf.domain.orders import Order, OrderSide
from btf.risk.manager import PortfolioRiskView, RiskVerdict

logger = logging.getLogger(__name__)


class MaxWeightRule:
    """单票最大权重（04 §8.3.4）：买入后 symbol 市值 / NAV ≤ max_weight。

    预估口径：决策时点盯市价（portfolio.close_of）× (持仓 + 订单数量)。
    超限 → REDUCE_TO(权重内最大整手)；已持满 → REJECT。
    """

    contract_version = CONTRACT_VERSION

    def __init__(self, max_weight: float = 0.10, lot_size: int = 100,
                 *, strict: bool = True):
        if not 0 < max_weight <= 1:
            raise ValueError(f"max_weight 须 ∈(0,1]，得 {max_weight}")
        self.max_weight = max_weight
        self.lot_size = lot_size
        #: 数据缺失语义（19 号审查 P0-3；**Q1 裁决 A —— X-7**）：True=
        #: fail-closed 拒单（**新默认**）；False=降级放行（历史口径，撮合层
        #: 兜底）。引擎⑧已对 drafts 补当日盯市价 → 正常路径下 strict 不触发
        #: （实测：样例 728 日 201 成交不变）；仅在**真无价**时拒单。
        #: 需保留历史宽松语义者显式配 `risk.rules.params.strict=false`。
        self.strict = strict

    def check(self, order: Order, portfolio: PortfolioRiskView,
              states: Mapping[str, TradingState]) -> RiskVerdict:
        if order.side is not OrderSide.BUY:
            return RiskVerdict.allow()                 # 卖出降权重
        price = portfolio.close_of(order.symbol)
        if not price or price <= 0:
            if self.strict:
                return RiskVerdict.reject(
                    "risk:max_weight: 无盯市价（strict 拒单）")
            logger.debug("max_weight 降级：无盯市价 %s（撮合层兜底）", order.symbol)
            return RiskVerdict.allow()
        pos = portfolio.positions.get(order.symbol)
        held_value = (pos.qty * price) if pos is not None and pos.qty > 0 else 0.0
        budget = self.max_weight * portfolio.total_value() - held_value
        if budget <= 0:
            return RiskVerdict.reject("risk:max_weight: 持仓已达权重上限")
        allowed = int(budget // (price * self.lot_size)) * self.lot_size
        if allowed >= order.qty:
            return RiskVerdict.allow()
        if allowed <= 0:
            return RiskVerdict.reject("risk:max_weight: 权重预算不足一手")
        return RiskVerdict.reduce_to(allowed)


class CashCheckRule:
    """现金充足校验（04 §8.3.4）：批次内买入累计耗现 ≤ 可用现金。

    批次语义：同批订单逐单扣减额度（on_batch_start 由链管理器在每批
    pre_check 开头重置）；费用以 fee_buffer 系数近似（佣金+过户量级）。
    不足 → REDUCE_TO(现金内最大整手)；一手都买不起 → REJECT。
    卖出不回补额度（保守——回笼在撮合后，时序上不可用于本批预估）。
    """

    contract_version = CONTRACT_VERSION

    def __init__(self, fee_buffer: float = 0.001, lot_size: int = 100,
                 *, strict: bool = True):
        if fee_buffer < 0:
            raise ValueError(f"fee_buffer 须 ≥0，得 {fee_buffer}")
        self.fee_buffer = fee_buffer
        self.lot_size = lot_size
        self.strict = strict          # 数据缺失语义：见 MaxWeightRule（P0-3/X-7）
        self._available = 0.0

    def on_batch_start(self, portfolio: PortfolioRiskView) -> None:
        self._available = portfolio.cash

    def check(self, order: Order, portfolio: PortfolioRiskView,
              states: Mapping[str, TradingState]) -> RiskVerdict:
        if order.side is not OrderSide.BUY:
            return RiskVerdict.allow()
        price = portfolio.close_of(order.symbol)
        if not price or price <= 0:
            if self.strict:
                return RiskVerdict.reject(
                    "risk:cash_check: 无盯市价（strict 拒单）")
            logger.debug("cash_check 降级：无盯市价 %s（撮合层兜底）", order.symbol)
            return RiskVerdict.allow()
        unit = price * (1 + self.fee_buffer)
        cost = order.qty * unit
        if cost <= self._available:
            self._available -= cost
            return RiskVerdict.allow()
        allowed = int(self._available // (unit * self.lot_size)) * self.lot_size
        if allowed <= 0:
            return RiskVerdict.reject("risk:cash: 可用现金不足一手")
        self._available -= allowed * unit
        return RiskVerdict.reduce_to(allowed)


class TradabilityRule:
    """停牌/退市/涨跌停预拒（04 §8.3.4）：T 日状态面板的事前否决。

    与撮合层拒单同语义双时点（互补）：停牌/退市 → 全方向拒；
    收盘涨停 → 买入拒（买不进）；收盘跌停 → 卖出拒（卖不出）；
    ST 默认仅披露（reject_st=True 时拒）。
    """

    contract_version = CONTRACT_VERSION

    def __init__(self, reject_st: bool = False, *, strict: bool = True):
        self.reject_st = reject_st
        self.strict = strict          # 数据缺失语义：见 MaxWeightRule（P0-3/X-7）

    def check(self, order: Order, portfolio: PortfolioRiskView,
              states: Mapping[str, TradingState]) -> RiskVerdict:
        state = states.get(order.symbol)
        if state is None:
            if self.strict:
                return RiskVerdict.reject(
                    "risk:tradability: 无状态面板（strict 拒单）")
            logger.debug("tradability 降级：无状态 %s（撮合层兜底）", order.symbol)
            return RiskVerdict.allow()
        if state.is_delisted:
            return RiskVerdict.reject("risk:delisted")
        if state.is_suspended:
            return RiskVerdict.reject("risk:suspended")
        if self.reject_st and state.is_st:
            return RiskVerdict.reject("risk:st")
        if state.is_limit_up and order.side is OrderSide.BUY:
            return RiskVerdict.reject("risk:limit_up: 收盘涨停买不进")
        if state.is_limit_down and order.side is OrderSide.SELL:
            return RiskVerdict.reject("risk:limit_down: 收盘跌停卖不出")
        return RiskVerdict.allow()


__all__ = ["CashCheckRule", "MaxWeightRule", "TradabilityRule"]
