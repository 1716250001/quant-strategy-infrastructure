# -*- coding: utf-8 -*-
"""L1 单测：domain 值对象（PoC-1 任务 1.1）。

锚点：04 §8.2.1-8.2.3 签名语义；E2 ETF 子类规则；TradingDate 互转。
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest
from btf.domain.market import (
    LIMIT_PRICE_TOL,
    Bar,
    touch_limit_down,
    touch_limit_up,
)
from btf.domain.types import (
    AssetClass,
    EtfSubclass,
    Instrument,
    TradingDate,
    rule_for,
)


@pytest.mark.l1
class TestTradingDate:
    def test_frozen_immutable(self):
        td = TradingDate(date(2024, 6, 28))
        with pytest.raises(FrozenInstanceError):
            td.iso = date(2024, 1, 1)  # type: ignore[misc]

    def test_ordering(self):
        assert TradingDate(date(2024, 1, 1)) < TradingDate(date(2024, 1, 2))
        assert TradingDate(date(2024, 1, 1)) == TradingDate(date(2024, 1, 1))

    def test_ymd_roundtrip(self):
        td = TradingDate.from_ymd("20240628")
        assert td.iso == date(2024, 6, 28)
        assert td.to_ymd() == "20240628"

    def test_ymd_rejects_bad(self):
        for bad in ("2024-06-28", "2024062", "2024062a", ""):
            with pytest.raises(ValueError):
                TradingDate.from_ymd(bad)


@pytest.mark.l1
class TestInstrument:
    def test_value_equality(self):
        """dataclass 值对象语义：全字段相等才相等。"""
        a = Instrument(symbol="000001.SZ", asset_class=AssetClass.STOCK)
        b = Instrument(symbol="000001.SZ", asset_class=AssetClass.STOCK)
        c = Instrument(symbol="000001.SZ", asset_class=AssetClass.STOCK, name="平安银行")
        assert a == b
        assert a != c  # name 参与值等值（同一标的快照须一致）

    def test_delisted_is_first_class(self):
        """退市股一等公民（幸存者偏差治理）：delist_date 非空可构造、可入池。"""
        inst = Instrument(
            symbol="600002.SH", asset_class=AssetClass.STOCK,
            list_date=TradingDate(date(1997, 7, 28)),
            delist_date=TradingDate(date(2019, 5, 17)),
        )
        assert inst.delist_date is not None


@pytest.mark.l1
class TestBoardRuleE2:
    """终审 E2：T+N 按资产 × 子类双维。"""

    def test_stock_t1(self):
        inst = Instrument(symbol="000001.SZ", asset_class=AssetClass.STOCK, board="main")
        assert rule_for(inst).t_plus == 1

    def test_gem_star_20pct(self):
        gem = Instrument(symbol="300001.SZ", asset_class=AssetClass.STOCK, board="gem")
        star = Instrument(symbol="688001.SH", asset_class=AssetClass.STOCK, board="star")
        assert rule_for(gem).limit_up_pct == 0.20
        assert rule_for(star).limit_up_pct == 0.20

    def test_etf_subclass_t_plus(self):
        """股票型 ETF=T+1；债券/黄金/跨境/货币=T+0（池内 511180/511380 实例）。"""
        stock_etf = Instrument(
            symbol="510300.SH", asset_class=AssetClass.ETF, etf_subclass=EtfSubclass.STOCK
        )
        assert rule_for(stock_etf).t_plus == 1
        for sub in (EtfSubclass.BOND, EtfSubclass.GOLD, EtfSubclass.CROSS_BORDER, EtfSubclass.MONEY):
            etf = Instrument(symbol="511180.SH", asset_class=AssetClass.ETF, etf_subclass=sub)
            assert rule_for(etf).t_plus == 0, f"{sub} 应为 T+0"

    def test_etf_default_subclass_is_stock(self):
        etf = Instrument(symbol="510300.SH", asset_class=AssetClass.ETF)
        assert rule_for(etf).t_plus == 1


@pytest.mark.l1
class TestBarAndLimit:
    def _bar(self, close: float) -> Bar:
        return Bar(
            symbol="X", date=TradingDate(date(2024, 1, 2)),
            open=close, high=close, low=close, close=close,
            volume=100.0, amount=1000.0, pre_close=close,
        )

    def test_bar_units_contract(self):
        """单位契约在 docstring；此处锁定字段存在性与类型。"""
        b = self._bar(10.0)
        assert (b.volume, b.amount) == (100.0, 1000.0)

    def test_touch_limit_tolerance(self):
        """涨跌停触及容差 1e-4（相对比较）——黄金集 G2 断言的数值基础。"""
        assert touch_limit_up(10.0, 10.0)                 # 恰好
        assert touch_limit_up(10.0 * (1 - LIMIT_PRICE_TOL), 10.0)   # 容差内
        assert not touch_limit_up(10.0 * (1 - 2 * LIMIT_PRICE_TOL), 10.0)  # 容差外
        assert touch_limit_down(9.0, 9.0)
        assert touch_limit_down(9.0 * (1 + LIMIT_PRICE_TOL), 9.0)
        assert not touch_limit_down(9.0 * (1 + 2 * LIMIT_PRICE_TOL), 9.0)

    def test_no_limit_constraint(self):
        assert not touch_limit_up(100.0, None)
        assert not touch_limit_down(0.1, None)
        assert not touch_limit_up(100.0, 0.0)   # 非法涨停价（0/负）视为无约束
