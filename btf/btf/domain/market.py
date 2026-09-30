# -*- coding: utf-8 -*-
"""市场数据值对象：Bar / TradingState（04 §8.2.3）。

TradingState 是现有数据资产未提供、回测工具必须补上的关键聚合层
（评审 H1 立项的"交易日状态面板"）：per (symbol, date) 的可交易性合成事实。
"""
from __future__ import annotations

from dataclasses import dataclass

from btf.domain.types import TradingDate


@dataclass(frozen=True, slots=True)
class Bar:
    """单标的单日 OHLCV（日线）。

    单位契约（05 §9.4 T-01）：价格为**不复权原始价**（元）；volume 单位**股**
    （适配层已从"手"×100 换算）；amount 单位**元**（适配层已从"千元"×1000）。
    复权在消费侧由 AdjustService 换算（引擎记账=名义价，ADR-6）。

    性能注记：slots=True（热路径对象，PoC-1 B1 实测 ~1.7μs/个 → 提速 ~35%）。
    """

    symbol: str
    date: TradingDate
    open: float
    high: float
    low: float
    close: float
    volume: float        # 股
    amount: float        # 元
    pre_close: float     # 前收盘（不复权）


@dataclass(frozen=True)
class TradingState:
    """交易日状态面板：per (symbol, date) 的合成事实。

    数据源（05 §9.4）：stk_limit（涨跌停价）+ suspend_d（停牌，口径=在表即停）
    + namechange（ST 区间判定）+ stock_basic（退市）。
    """

    symbol: str
    date: TradingDate
    limit_up_price: float | None    # 涨停价（None=无涨跌幅制度或数据缺失→降级）
    limit_down_price: float | None
    is_suspended: bool
    is_st: bool
    is_delisted: bool
    is_limit_up: bool               # 收盘触及涨停（容差 1e-4 相对比较）
    is_limit_down: bool

    @property
    def tradable(self) -> bool:
        """综合可交易：非停牌 ∧ 非退市（有行情由截面存在性表达）。"""
        return not (self.is_suspended or self.is_delisted)


#: 涨跌停触及判定的浮点容差（09 §14.2：测试锚定 1e-4 相对比较）
LIMIT_PRICE_TOL = 1e-4


def touch_limit_up(close: float, limit_up: float | None) -> bool:
    """收盘价是否触及涨停（相对容差比较；None=无涨停约束→False）。"""
    if limit_up is None or limit_up <= 0:
        return False
    return close >= limit_up * (1 - LIMIT_PRICE_TOL)


def touch_limit_down(close: float, limit_down: float | None) -> bool:
    """收盘价是否触及跌停。"""
    if limit_down is None or limit_down <= 0:
        return False
    return close <= limit_down * (1 + LIMIT_PRICE_TOL)


__all__ = ["LIMIT_PRICE_TOL", "Bar", "TradingState", "touch_limit_down", "touch_limit_up"]
