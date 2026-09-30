# -*- coding: utf-8 -*-
"""分段费率与滑点模型单测（M2 任务 5.1；09 §14.2 费用断言 + G7 数值锚点）。

分段各测（H3/N2）：
    - 印花税 2023-08-28 前后两档（0.1% → 0.05%）；仅卖出
    - 过户费三段：2015-08 前仅沪市（面额口径）/ 2015-08 起沪深双向 0.002%
      / 2022-04-29 起 0.001%
    - 佣金最低 5 元触发
手算锚点全部逐笔列于各断言旁（公式唯一出处 04 §8.2.6/§8.2.4 对照）。
"""
from __future__ import annotations

import pytest
from btf.domain.orders import Order, OrderSide, OrderType
from btf.domain.types import TradingDate
from btf.execution.cost import (
    A_SHARE_SEGMENTS,
    AShareTieredFeeModel,
    FeeSegment,
    FixedSlippage,
    FlatRateCostModel,
    NoSlippage,
    PctSlippage,
    ZeroCostModel,
)
from btf.registry import (
    COST_MODEL,
    SLIPPAGE_MODEL,
    RegistryError,
    available,
    create,
    resolve,
)

D = TradingDate.from_ymd


def _order(side: OrderSide, symbol: str = "000001.SZ", qty: int = 100) -> Order:
    return Order(order_id="O1", symbol=symbol, side=side,
                 order_type=OrderType.MARKET, qty=qty, limit_price=None,
                 created_at=D("20230101"))


class TestStampDutySegments:
    """印花税两段（H3）：2023-08-28 起 0.05%，此前 0.1%；仅卖出单边。"""

    def test_before_20230828_sell(self):
        m = AShareTieredFeeModel()
        # 卖 10000 股 @20 → turnover=200000：comm=50；stamp=200000×0.001=200
        f = m.fees("000001.SZ", OrderSide.SELL, 10_000, 20.0, D("20230827"))
        assert f.stamp_duty == 200.0
        assert f.commission == 50.0

    def test_on_20230828_sell_half(self):
        m = AShareTieredFeeModel()
        # 边界日当日生效：stamp=200000×0.0005=100（若未分段应为 200）
        f = m.fees("000001.SZ", OrderSide.SELL, 10_000, 20.0, D("20230828"))
        assert f.stamp_duty == 100.0

    def test_buy_never_stamp(self):
        m = AShareTieredFeeModel()
        for d in ("20230827", "20230828"):
            f = m.fees("000001.SZ", OrderSide.BUY, 10_000, 20.0, D(d))
            assert f.stamp_duty == 0.0


class TestTransferFeeSegments:
    """过户费三段（N2）：仅沪→双向；面额口径→金额口径；两档费率。"""

    def test_pre2015_sh_par_basis(self):
        m = AShareTieredFeeModel()
        # 2014 沪市：transfer = 1000 股 × 1 元面值 × 0.0006 = 0.60
        f = m.fees("600000.SH", OrderSide.BUY, 1_000, 10.0, D("20140102"))
        assert f.transfer_fee == 0.60

    def test_pre2015_sz_exempt(self):
        m = AShareTieredFeeModel()
        # 2015-08 前深市不收：transfer=0
        f = m.fees("000001.SZ", OrderSide.BUY, 1_000, 10.0, D("20140102"))
        assert f.transfer_fee == 0.0

    def test_from2015_both_2bp(self):
        m = AShareTieredFeeModel()
        # 2016 深市双向 0.002%：turnover=10000 × 0.00002 = 0.20
        f = m.fees("000001.SZ", OrderSide.BUY, 1_000, 10.0, D("20160104"))
        assert f.transfer_fee == 0.20

    def test_from20220429_1bp(self):
        m = AShareTieredFeeModel()
        # 2022-04-29 起 0.001%：turnover=200000 × 0.00001 = 2.00
        f = m.fees("600000.SH", OrderSide.SELL, 10_000, 20.0, D("20220429"))
        assert f.transfer_fee == 2.00

    def test_boundary_days(self):
        m = AShareTieredFeeModel()
        # 2015-07-31（沪 par）0.60 → 2015-08-01（双向 amount）10000×0.00002=0.20
        assert m.fees("600000.SH", OrderSide.BUY, 1_000, 10.0,
                      D("20150731")).transfer_fee == 0.60
        assert m.fees("600000.SH", OrderSide.BUY, 1_000, 10.0,
                      D("20150801")).transfer_fee == 0.20
        # 2022-04-28 0.002% → 2022-04-29 0.001%
        assert m.fees("600000.SH", OrderSide.BUY, 1_000, 10.0,
                      D("20220428")).transfer_fee == 0.20
        assert m.fees("600000.SH", OrderSide.BUY, 1_000, 10.0,
                      D("20220429")).transfer_fee == 0.10


class TestCommission:
    """佣金：万 2.5 双向 + 单笔最低 5 元。"""

    def test_min_commission_triggered(self):
        m = AShareTieredFeeModel()
        # turnover=100 → 100×2.5e-4=0.025 < 5 → 佣金 5 元
        f = m.fees("000001.SZ", OrderSide.BUY, 100, 1.0, D("20200102"))
        assert f.commission == 5.0

    def test_rate_commission_when_large(self):
        m = AShareTieredFeeModel()
        # turnover=1_000_000 → 250 元（费率档）
        f = m.fees("000001.SZ", OrderSide.BUY, 100_000, 10.0, D("20200102"))
        assert f.commission == 250.0


class TestG7Anchors:
    """G7 数值锚点：给定 (symbol, side, qty, price, date) 逐笔手算对照。"""

    def test_anchor_2014_sh_sell(self):
        m = AShareTieredFeeModel()
        # 沪市卖 1000@10（2014）：comm=max(2.5,5)=5；stamp=10；transfer=0.60
        f = m.fees("600000.SH", OrderSide.SELL, 1_000, 10.0, D("20140102"))
        assert (f.commission, f.stamp_duty, f.transfer_fee) == (5.0, 10.0, 0.60)
        assert f.total == 15.60

    def test_anchor_2023_buy(self):
        m = AShareTieredFeeModel()
        # 买 1000@10（2023-01，双向 0.001%）：comm=5；stamp=0；transfer=0.10
        f = m.fees("600000.SH", OrderSide.BUY, 1_000, 10.0, D("20230103"))
        assert (f.commission, f.stamp_duty, f.transfer_fee) == (5.0, 0.0, 0.10)
        assert f.total == 5.10

    def test_anchor_2024_sell_full(self):
        m = AShareTieredFeeModel()
        # 卖 10000@20（2024）：comm=50；stamp=100；transfer=2.00 → 152.00
        f = m.fees("000001.SZ", OrderSide.SELL, 10_000, 20.0, D("20240102"))
        assert (f.commission, f.stamp_duty, f.transfer_fee) == (50.0, 100.0, 2.0)
        assert f.total == 152.0

    def test_total_equals_sum(self):
        m = AShareTieredFeeModel()
        f = m.fees("600000.SH", OrderSide.SELL, 3_300, 7.77, D("20190102"))
        assert f.total == round(f.commission + f.stamp_duty + f.transfer_fee, 2)


class TestSegmentTable:
    """段表结构纪律：None 首段 / 升序 / 未覆盖日期显式报错。"""

    def test_builtin_first_segment_open_ended(self):
        assert A_SHARE_SEGMENTS[0].effective_from is None

    def test_uncovered_date_raises(self):
        m = AShareTieredFeeModel(
            segments=(FeeSegment(effective_from=TradingDate.from_ymd("20200101"),
                                 stamp_duty_sell=0.001,
                                 transfer_fee_rate=0.00001),))
        with pytest.raises(ValueError, match="未覆盖"):
            m.fees("000001.SZ", OrderSide.BUY, 100, 10.0, D("19990101"))


class TestSlippageModels:
    """滑点：固定/百分比，买卖方向；纯函数确定性。"""

    def test_no_slippage_identity(self):
        assert NoSlippage().apply(_order(OrderSide.BUY), 10.0, None, None) == 10.0
        assert NoSlippage().apply(_order(OrderSide.SELL), 10.0, None, None) == 10.0

    def test_fixed_slippage_direction(self):
        s = FixedSlippage(amount=0.01)
        assert s.apply(_order(OrderSide.BUY), 10.0, None, None) == pytest.approx(10.01)
        assert s.apply(_order(OrderSide.SELL), 10.0, None, None) == pytest.approx(9.99)

    def test_pct_slippage_direction(self):
        s = PctSlippage(pct=0.001)
        assert s.apply(_order(OrderSide.BUY), 10.0, None, None) == pytest.approx(10.01)
        assert s.apply(_order(OrderSide.SELL), 10.0, None, None) == pytest.approx(9.99)

    def test_deterministic_pure_function(self):
        s = PctSlippage(pct=0.002)
        o = _order(OrderSide.BUY)
        assert s.apply(o, 7.35, None, None) == s.apply(o, 7.35, None, None)


class TestLegacyAndZero:
    """ZeroCostModel 全零；FlatRateCostModel 现行常数（date 参数兼容）。"""

    def test_zero_cost(self):
        f = ZeroCostModel().fees("600000.SH", OrderSide.SELL, 1_000, 10.0,
                                 D("20140102"))
        assert (f.commission, f.stamp_duty, f.transfer_fee, f.total) == (0, 0, 0, 0)

    def test_flat_rate_current_constants(self):
        # 现行口径：comm=max(100000×2.5e-4,5)=25；stamp=100000×5e-4=50；
        # transfer=100000×1e-5=1
        f = FlatRateCostModel().fees("600000.SH", OrderSide.SELL, 10_000, 10.0,
                                     D("19990101"))       # date 不影响（常数）
        assert (f.commission, f.stamp_duty, f.transfer_fee) == (25.0, 50.0, 1.0)


class TestRegistryIntegration:
    """registry 声明制：tiered_v1/fixed/pct 可解析、可实例化、协商通过。"""

    def test_names_resolvable(self):
        for point, names in [
            (COST_MODEL, ["tiered_v1", "zero", "flat_rate"]),
            (SLIPPAGE_MODEL, ["none", "fixed", "pct"]),
        ]:
            avail = available(point)
            for n in names:
                assert n in avail, f"{point}:{n} 未登记"

    def test_create_tiered(self):
        m = create(COST_MODEL, "tiered_v1")
        assert isinstance(m, AShareTieredFeeModel)
        assert m.contract_version == "1.0"

    def test_create_slippage_with_params(self):
        s = create(SLIPPAGE_MODEL, "pct", {"pct": 0.0005})
        assert isinstance(s, PctSlippage) and s.pct == 0.0005
        s2 = create(SLIPPAGE_MODEL, "fixed", {"amount": 0.02})
        assert isinstance(s2, FixedSlippage) and s2.amount == 0.02

    def test_unknown_name_still_explicit(self):
        with pytest.raises(RegistryError, match="未知插件名"):
            resolve(COST_MODEL, "no_such")
