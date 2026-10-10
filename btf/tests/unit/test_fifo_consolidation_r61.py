# -*- coding: utf-8 -*-
"""**R61′（P1；N63-a/N63-b；19 号 §113.2/§113.3/§114.2）**：折算感知 FIFO 配对。

覆盖（§114.4 ② 五条反例，缺一不可）：
    - **R-1** 折算后**部分卖出** ⇒ 队列剩余按 R 正确缩减；
    - **R-2** 同一标的**两次折算** ⇒ 变换**累积**非覆盖；
    - **R-3** 折算后**再买入**、随后卖出 ⇒ **新 lot 不被 ×R**（仅 ex_date 前 lot）；
    - **R-4** **折算日 == 买入日**（同日边界）⇒ 当日 lot 不参与该次折算；
    - **R-5** **无折算**标的 ⇒ 逐位不变（阴性对照）；
    - 另：`Σ 未平仓 qty == 末期持仓`、**单笔 pnl 符号**（亏损须为负）、
      R21′ 保底（`int(剩余×R)==0` ⇒ 1 份、成本价不变）、
      `normalize_consolidations` 归一化（脏值/去重/排序/映射形态）。
数值锚：510580 实测（§111.2/§113.2）——买 187,500 @0.6276 → 折算
R=0.2479552 → 卖 @2.278 ⇒ 调整成本 **2.5311**、pnl **为负**（≈ −11,920）。
"""
from __future__ import annotations

import logging

import pytest
from btf.analytics.trades import normalize_consolidations, pair_fills_to_trades
from btf.domain.orders import Fee, Fill, OrderSide
from btf.domain.types import TradingDate

D = TradingDate.from_ymd


def _fill(ymd: str, side: OrderSide, qty: int, price: float, *,
          symbol: str = "510580.SH", fid: str = "", fee: float = 0.0) -> Fill:
    n = f"{ymd}-{side.value}-{qty}-{price}"
    return Fill(fill_id=fid or n, order_id=f"O-{n}", symbol=symbol, side=side,
                qty=qty, price=price,
                fee=Fee(commission=fee, stamp_duty=0.0, transfer_fee=0.0,
                        total=fee),
                fill_date=D(ymd), fill_timing="open")


def _cons(symbol: str, ymd: str, ratio: float) -> tuple[str, TradingDate, float]:
    return (symbol, D(ymd), ratio)


# ───────────────────────── R-1：部分卖出 ─────────────────────────

def test_r1_partial_sell_remainder_scaled():
    """折算后**部分卖出** ⇒ 剩余 = 折算后量 − 卖出量；pnl 用调整成本（**为负**）。"""
    fills = [
        _fill("20240621", OrderSide.BUY, 187_500, 0.6276, fee=62.76),
        _fill("20240903", OrderSide.SELL, 20_000, 2.278, fee=2.28),
    ]
    cons = [_cons("510580.SH", "20240902", 0.2479552)]
    trades = pair_fills_to_trades(fills, consolidations=cons)
    closed = [t for t in trades if t.close_fill is not None]
    open_ = [t for t in trades if t.close_fill is None]
    assert len(closed) == 1 and len(open_) == 1
    # 折算后 46,491（int(187500×0.2479552)）；卖出 20,000 ⇒ 未平仓 26,491
    assert closed[0].qty == 20_000
    assert open_[0].qty == 26_491
    # 调整成本 = 0.6276 ÷ 0.2479552 ≈ 2.5311 ⇒ 卖出价 2.278 为**亏损**
    assert closed[0].pnl < 0
    adj_price = 0.6276 / 0.2479552
    adj_orig = 187_500 * 0.2479552            # 调整后原始量（费用摊销分母）
    expected = ((2.278 * 20_000 - 2.28)        # 平仓收入（扣平仓费）
                - (adj_price * 20_000 + 62.76 * (20_000 / adj_orig)))
    assert closed[0].pnl == pytest.approx(expected, rel=1e-9)


def test_r1_unclosed_sum_equals_position():
    """`Σ 未平仓 qty == 末期持仓`（§111.3 的判定式；虚高 168,109 的回归锚）。"""
    fills = [
        _fill("20240621", OrderSide.BUY, 187_500, 0.6276),
        _fill("20240903", OrderSide.SELL, 46_491, 2.278),
    ]
    cons = [_cons("510580.SH", "20240902", 0.2479552)]
    trades = pair_fills_to_trades(fills, consolidations=cons)
    assert sum(t.qty for t in trades if t.close_fill is None) == 0
    # 无折算感知（旧行为）则会残留 141,009 —— 本断言即 N63-a 捕获网
    old = pair_fills_to_trades(fills)
    assert sum(t.qty for t in old if t.close_fill is None) == 141_009


# ───────────────────────── R-2：两次折算（累积） ─────────────────────────

def test_r2_two_consolidations_accumulate():
    fills = [
        _fill("20240101", OrderSide.BUY, 10_000, 4.0, symbol="X.SH"),
        _fill("20240301", OrderSide.SELL, 2_000, 20.0, symbol="X.SH"),
        _fill("20240501", OrderSide.SELL, 1_000, 40.0, symbol="X.SH"),
    ]
    cons = [_cons("X.SH", "20240201", 0.5), _cons("X.SH", "20240401", 0.5)]
    trades = pair_fills_to_trades(fills, consolidations=cons)
    closes = [t for t in trades if t.close_fill is not None]
    # 10,000 ×0.5 = 5,000（第一次）；5,000 − 2,000 = 3,000 → ×0.5 = 1,500（第二次）
    assert [t.qty for t in closes] == [2_000, 1_000]
    # 成本累积：4.0 ÷0.5 = 8.0；再过一次 ÷0.5 = 16.0（非覆盖为 8.0）
    assert closes[0].pnl == pytest.approx((20.0 - 8.0) * 2_000)
    assert closes[1].pnl == pytest.approx((40.0 - 16.0) * 1_000)
    assert sum(t.qty for t in trades if t.close_fill is None) == 500   # 1,500 − 1,000


# ───────────────────────── R-3：折算后再买入 ─────────────────────────

def test_r3_new_lot_after_consolidation_not_scaled():
    """折算**之后**建立的 lot 不受该次折算影响（§114.2 第 4 条）。"""
    fills = [
        _fill("20240101", OrderSide.BUY, 1_000, 10.0, symbol="X.SH"),    # 旧 lot（受影响）
        _fill("20240301", OrderSide.BUY, 1_000, 5.0, symbol="X.SH"),     # 新 lot（不受影响）
        _fill("20240401", OrderSide.SELL, 1_500, 6.0, symbol="X.SH"),    # FIFO 先吃旧 lot
    ]
    cons = [_cons("X.SH", "20240201", 0.5)]
    trades = pair_fills_to_trades(fills, consolidations=cons)
    closed = [t for t in trades if t.close_fill is not None]
    assert [t.qty for t in closed] == [500, 1_000]
    # 旧 lot：500 × (调整价 20.0)；新 lot：1,000 × 原价 5.0（**未 ÷R**）
    assert closed[0].pnl == pytest.approx((6.0 - 20.0) * 500)
    assert closed[1].pnl == pytest.approx((6.0 - 5.0) * 1_000)
    assert sum(t.qty for t in trades if t.close_fill is None) == 0


# ───────────────────────── R-4：折算日 == 买入日 ─────────────────────────

def test_r4_consolidation_on_buy_date_boundary():
    """同日边界：**当日买入**的 lot 不参与该次折算；当日卖出按折算后量配对。"""
    fills = [
        _fill("20240101", OrderSide.BUY, 1_000, 10.0, symbol="X.SH"),
        _fill("20240201", OrderSide.BUY, 300, 5.0, symbol="X.SH"),        # 折算日当日买入
        _fill("20240301", OrderSide.SELL, 800, 6.0, symbol="X.SH"),
    ]
    cons = [_cons("X.SH", "20240201", 0.5)]
    trades = pair_fills_to_trades(fills, consolidations=cons)
    closed = [t for t in trades if t.close_fill is not None]
    assert [t.qty for t in closed] == [500, 300]
    assert closed[0].pnl == pytest.approx((6.0 - 20.0) * 500)   # 老 lot ÷R
    assert closed[1].pnl == pytest.approx((6.0 - 5.0) * 300)    # 当日 lot 不调整
    assert sum(t.qty for t in trades if t.close_fill is None) == 0


# ───────────────────────── R-5：阴性对照 ─────────────────────────

def test_r5_no_consolidation_bitwise_identical():
    """无折算：`None` / `()` / **他标的折算** 三态输出**逐位相同**（阴性对照）。"""
    fills = [
        _fill("20240101", OrderSide.BUY, 1_000, 10.0, symbol="R5.SH"),
        _fill("20240201", OrderSide.SELL, 400, 11.0, symbol="R5.SH"),
        _fill("20240301", OrderSide.BUY, 200, 9.0, symbol="R5.SH"),
    ]
    base = pair_fills_to_trades(fills)
    assert pair_fills_to_trades(fills, consolidations=None) == base
    assert pair_fills_to_trades(fills, consolidations=[]) == base
    assert pair_fills_to_trades(
        fills, consolidations=[_cons("OTHER.SH", "20240201", 0.5)]) == base


def test_r5_tail_consolidation_affects_open_lot():
    """**末次成交之后**的折算仍须生效（否则未平仓量与末日持仓不符）。"""
    fills = [_fill("20240101", OrderSide.BUY, 1_000, 10.0, symbol="X.SH")]
    trades = pair_fills_to_trades(
        fills, consolidations=[_cons("X.SH", "20240201", 0.5)])
    assert [t.qty for t in trades] == [500]           # 未平仓按折算后量
    assert trades[0].close_fill is None and trades[0].pnl == 0.0


# ───────────────────────── 保底（R21′ 同源） ─────────────────────────

def test_forced_min_qty_keeps_cost_basis():
    """`int(剩余×R)==0` ⇒ **保底 1 份、成本价不变**（与组合层 R21′ 同源）。"""
    fills = [_fill("20240101", OrderSide.BUY, 1, 10.0, symbol="X.SH")]
    trades = pair_fills_to_trades(
        fills, consolidations=[_cons("X.SH", "20240201", 0.4)])
    assert [t.qty for t in trades] == [1]             # 清零被禁（N59 同族）


# ───────────────────────── normalize_consolidations ─────────────────────────

class TestNormalize:
    def test_mapping_form_and_sort(self):
        rows = [
            {"symbol": "B.SH", "ex_date": "20240301", "ratio": 0.5},
            {"symbol": "A.SH", "ex_date": "2024-02-01", "ratio": 0.25},
        ]
        out = normalize_consolidations(rows)
        assert [r[0] for r in out] == ["A.SH", "B.SH"]        # 按 ex_date 升序
        assert out[0][1] == D("20240201") and out[0][2] == 0.25

    def test_ratio_one_dropped(self):
        assert normalize_consolidations(
            [("A.SH", D("20240201"), 1.0)]) == ()

    def test_invalid_values_warn_and_drop(self, caplog):
        with caplog.at_level(logging.WARNING, logger="btf.analytics.trades"):
            out = normalize_consolidations([
                ("A.SH", D("20240201"), ""),          # 不可转 ⇒ 1.0＋WARN
                ("A.SH", D("20240201"), 0.0),         # ≤0 ⇒ 无效值＋WARN（禁清零）
                ("A.SH", "not-a-date", 0.5),          # 日期不可解析 ⇒ 跳过＋WARN
                "garbage",                            # 形态不可识别 ⇒ 跳过＋WARN
            ])
        assert out == ()
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "不可转换" in text and "≤0" in text and "不可解析" in text

    def test_none_and_empty(self):
        assert normalize_consolidations(None) == ()
        assert normalize_consolidations(()) == ()


def test_consolidations_from_events_roundtrip(tmp_path):
    """复核路径来源（`events.jsonl`）可还原已应用折算（`bt verify` 同源）。"""
    from btf.domain.action import CorporateAction
    from btf.domain.events import CorporateActionEvent
    from btf.engine.events_log import EventLogWriter

    path = tmp_path / "events.jsonl"
    with EventLogWriter(path) as w:
        w.write(CorporateActionEvent(action=CorporateAction(
            symbol="510580.SH", ex_date=D("20240902"), pay_date=None,
            record_date=None, cash_div_per_share=0.0, stk_div_per_share=0.0,
            ann_date=None, consolidation_ratio=0.2479552,
            consolidation_source="fund_conv")))
        w.write(CorporateActionEvent(action=CorporateAction(
            symbol="510880.SH", ex_date=D("20240903"), pay_date=D("20240904"),
            record_date=None, cash_div_per_share=0.03, stk_div_per_share=0.0,
            ann_date=None, consolidation_ratio=1.0)))       # 纯分红 ⇒ 剔除
    from btf.runtime.run_orchestrator import consolidations_from_events

    out = consolidations_from_events(path)
    assert len(out) == 1 and out[0][0] == "510580.SH"
    assert out[0][2] == pytest.approx(0.2479552)
    assert consolidations_from_events(tmp_path / "missing.jsonl") == ()
