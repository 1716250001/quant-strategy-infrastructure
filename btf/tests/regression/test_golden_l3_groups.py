# -*- coding: utf-8 -*-
"""L3 分组对账：G2–G9 黄金案例（引擎 vs 独立参考计算器 + 数据层独立复算）。

适用范围（M2 任务 5.6 补全）：G1 三案例的逐字段对账见
`test_golden_l3_reconcile.py`；本文件覆盖 **G2–G9**：

    G2 涨跌停   —— 拒单链 limit_up / limit_down（真实 stk_limit 限价）
    G3 停牌     —— 停牌日截面缺席 → suspended 拒单；复牌后成交
    G4 T+1/T+0  —— 同日买+卖：股票 t_plus_1 拒单 vs 债券 ETF 双成交（E2）
    G5 ST       —— 状态面板 is_st 区间（namechange 独立复算）
    G6 退市     —— 末日行情 + 退市后拒单 + 残值按末价携带
    G7 手数费用 —— lot_size 拒单 + 最低佣金 + 费率分段 + 卖先买后资金链
    G8 日历     —— 春节缺口 + W-FRI 周锚（两套独立实现互校）
    G9 复权链   —— 事件链收益 ≡ hfq 收益（adj_factor 独立复算）

对账容差 1e-10（09 §14.3）；两侧共享外部条件（tiered_v1 / next_open / full /
空风控链），差异只能归因于实现。
"""
from __future__ import annotations

import pytest

from tests.fixtures.golden import CASES_DIR, case_to_feed, load_case, run_case

pytestmark = [pytest.mark.l3]

TOL = 1e-10
ORDER_CASES = ["g2_limit_up_buy_rejected", "g2_limit_down_sell_rejected",
               "g3_suspension_buy_rejected", "g4_t1_same_day_sell_rejected",
               "g4_t0_etf_same_day_roundtrip", "g6_delist_last_day",
               "g7_lot_and_min_commission", "g7_fee_segments_pre2015",
               "g10_vpp_partial_fill", "g10_vpp_volume_cap_rejected"]
ALL_GROUPS = [*ORDER_CASES, "g5_st_interval", "g8_calendar_spring_festival",
              "g9_dividend_chain_hfq"]


def _require_cases() -> None:
    if not CASES_DIR.is_dir():
        pytest.skip(f"黄金集未构建: {CASES_DIR}（先跑 tools/build_golden.py）")


@pytest.fixture(scope="module")
def reconciled() -> dict[str, dict]:
    _require_cases()
    out = {}
    for case_id in ALL_GROUPS:
        case = load_case(case_id)
        out[case_id] = {"engine": run_case(case), "case": case}
    return out


def _codes(result) -> list[tuple[str, str, int, str]]:
    """引擎拒单 → (symbol, side, qty, code) 序列。"""
    return [(order.symbol, order.side.value, order.qty, rejection.code.value)
            for order, rejection in result.rejections]


class TestRejectionReconciliation:
    """拒单链逐条对账（引擎 vs 参考计算器；顺序亦须一致）。"""

    @pytest.mark.parametrize("case_id", ORDER_CASES)
    def test_rejections_match(self, reconciled, case_id):
        pkg = reconciled[case_id]
        want = [(r["symbol"], r["side"], r["qty"], r["code"])
                for r in pkg["case"]["assertions"]["rejections"]]
        assert _codes(pkg["engine"]) == want

    @pytest.mark.parametrize("case_id", ORDER_CASES)
    def test_rejected_counts_match(self, reconciled, case_id):
        counts: dict[str, int] = {}
        for _sym, _side, _qty, code in _codes(reconciled[case_id]["engine"]):
            counts[code] = counts.get(code, 0) + 1
        assert counts == reconciled[case_id]["case"]["assertions"]["rejected_counts"]

    @pytest.mark.parametrize("case_id", ORDER_CASES)
    def test_fills_and_nav_match(self, reconciled, case_id):
        pkg = reconciled[case_id]
        got, want = pkg["engine"], pkg["case"]["assertions"]
        assert len(got.fills) == len(want["fills"])
        for fill, expected in zip(got.fills, want["fills"], strict=True):
            assert fill.fill_date.to_ymd() == expected["date"]
            assert fill.side.value == expected["side"]
            assert fill.qty == expected["qty"]
            assert fill.price == pytest.approx(expected["price"], abs=TOL)
            for part in ("commission", "stamp_duty", "transfer_fee", "total"):
                assert getattr(fill.fee, part) == pytest.approx(
                    expected["fee"][part], abs=TOL), part
        for snap, expected in zip(got.snapshots, want["snapshots"], strict=True):
            assert snap.date.to_ymd() == expected["date"]
            for field in ("cash", "market_value", "total_value", "daily_return",
                          "cumulative_return", "drawdown"):
                assert getattr(snap, field) == pytest.approx(
                    expected[field], abs=TOL), f"{expected['date']}.{field}"
            assert dict(snap.positions_qty) == expected["positions_qty"]


class TestGroup2And3Semantics:
    """G2/G3 语义锚点（断言本身可读、可归因）。"""

    def test_limit_up_buy_rejected_at_open(self, reconciled):
        """涨停开盘价 = 买入拒绝价（开板前无法买入）。"""
        case = reconciled["g2_limit_up_buy_rejected"]["case"]
        rej = case["assertions"]["rejections"][0]
        day = rej["date"]
        bar = next(b for b in case["data"]["bars"] if b["date"] == day)
        limit_up = case["assertions"]["state_expectations"]["limits"][day][0]
        assert bar["open"] == pytest.approx(limit_up, abs=1e-9)
        assert reconciled["g2_limit_up_buy_rejected"]["engine"].fills == []

    def test_limit_down_sell_rejected_but_buy_allowed(self, reconciled):
        """跌停开盘：买入允许且成交，卖出被拒（跌停无接盘）。"""
        case = reconciled["g2_limit_down_sell_rejected"]["case"]
        fills = case["assertions"]["fills"]
        assert [f["side"] for f in fills] == ["buy"]
        assert case["assertions"]["rejected_counts"] == {"limit_down": 1}

    def test_suspension_days_have_no_bar(self, reconciled):
        """停牌日：日历内但**无 bar**（引擎截面缺席 → suspended 拒单）。

        例外：`suspend_d` 含**复牌日**记录（该日有 bar 但面板仍标停牌，见
        M3 6.3 留痕）——故断言「绝大多数停牌日无 bar」而非全等。
        """
        case = reconciled["g3_suspension_buy_rejected"]["case"]
        dates = set(case["data"]["dates"])
        bars = {b["date"] for b in case["data"]["bars"]}
        suspended = {d for d, flag in
                     case["assertions"]["state_expectations"]["suspended"].items()
                     if flag}
        assert len(suspended) >= 10, "案例须含长停牌区间"
        assert suspended <= dates                       # 停牌日在日历内
        with_bar = suspended & bars
        assert len(with_bar) <= 1, f"仅复牌日可有 bar，实测 {sorted(with_bar)}"
        assert case["assertions"]["rejected_counts"] == {"suspended": 1}


class TestGroup4TPlusSemantics:
    """G4：T+1 拒单 vs T+0 双成交（BoardRule 由资产 × 子类驱动，E2）。"""

    def test_stock_t1_rejects_same_day_sell(self, reconciled):
        pkg = reconciled["g4_t1_same_day_sell_rejected"]
        assert pkg["case"]["assertions"]["rejected_counts"] == {"t_plus_1": 1}
        assert [f["side"] for f in pkg["case"]["assertions"]["fills"]] == ["buy"]

    def test_bond_etf_t0_roundtrip_fills_both(self, reconciled):
        pkg = reconciled["g4_t0_etf_same_day_roundtrip"]
        case = pkg["case"]
        assert case["data"]["instruments"][0]["etf_subclass"] == "bond"
        assert case["data"]["instruments"][0]["t_plus"] == 0
        assert [f["side"] for f in case["assertions"]["fills"]] == ["buy", "sell"]
        assert case["assertions"]["rejected_counts"] == {}
        assert pkg["engine"].rejections == []


class TestGroup5StateFlags:
    """G5：ST 区间（状态面板 is_st 逐日）。"""

    def test_is_st_interval_non_trivial(self, reconciled):
        pkg = reconciled["g5_st_interval"]
        flags = pkg["case"]["assertions"]["state_expectations"]["is_st"]
        assert sum(flags.values()) >= 3, "案例须含 ≥3 个 ST 交易日"
        assert not all(flags.values()), "窗口须含非 ST 日（区间边界可见）"

    def test_engine_states_match_expectations(self, reconciled):
        pkg = reconciled["g5_st_interval"]
        feed, _instruments = case_to_feed(pkg["case"])
        from btf.domain.types import TradingDate

        symbol = pkg["case"]["data"]["instruments"][0]["symbol"]
        expected = pkg["case"]["assertions"]["state_expectations"]["is_st"]
        for ymd, want in expected.items():
            states = feed.trading_states(TradingDate.from_ymd(ymd), [symbol])
            assert states[symbol].is_st is want, ymd

    def test_st_does_not_block_trading_in_engine(self, reconciled):
        """ST 不是拒单码（拒单七 code 无 ST）——引擎不因 ST 拒单。"""
        assert reconciled["g5_st_interval"]["engine"].rejections == []


class TestGroup6Delisting:
    """G6：退市 —— 末日行情、退市后拒单、残值按末价携带。"""

    def test_delist_after_last_bar_rejected(self, reconciled):
        case = reconciled["g6_delist_last_day"]["case"]
        assert case["assertions"]["rejected_counts"] == {"suspended": 1}
        assert sum(case["assertions"]["state_expectations"]["delisted"].values()) >= 1

    def test_residual_valued_at_last_close(self, reconciled):
        """退市后无 bar → 市值沿用**末次已知收盘价**（镜像 Portfolio._last_close）。"""
        pkg = reconciled["g6_delist_last_day"]
        snaps = pkg["case"]["assertions"]["snapshots"]
        last_bar_date = max(b["date"] for b in pkg["case"]["data"]["bars"])
        after = [s for s in snaps if s["date"] > last_bar_date]
        assert after, "窗口须覆盖退市后交易日"
        closes = [s["market_value"] for s in after]
        assert len(set(round(c, 6) for c in closes)) == 1, "残值应在退市后保持不变"
        assert closes[0] > 0


class TestGroup7LotsAndFees:
    """G7：整手 / 最低佣金 / 费率分段 / 卖先买后资金链。"""

    def test_non_lot_buy_rejected(self, reconciled):
        pkg = reconciled["g7_lot_and_min_commission"]
        assert pkg["case"]["assertions"]["rejected_counts"] == {"lot_size": 1}
        rejection = pkg["case"]["assertions"]["rejections"][0]
        assert rejection["qty"] == 150 and rejection["side"] == "buy"

    def test_min_commission_applies(self, reconciled):
        """低价 × 1000 股：佣金 = max(万分之 2.5, 5 元) = 5 元（最低佣金生效）。"""
        fills = reconciled["g7_lot_and_min_commission"]["case"]["assertions"]["fills"]
        assert fills, "案例须有成交"
        for fill in fills:
            assert fill["fee"]["commission"] == pytest.approx(5.0, abs=1e-9)

    def test_pre2015_segment_transfer_fee_on_par(self, reconciled):
        """2015-08-01 前：过户费按**面额**万分之 6（沪市），印花税卖出千分之 1。"""
        pkg = reconciled["g7_fee_segments_pre2015"]
        for fill in pkg["case"]["assertions"]["fills"]:
            assert fill["fee"]["transfer_fee"] == pytest.approx(
                1000 * 1.0 * 0.0006, abs=1e-9)
            expected_stamp = (fill["price"] * fill["qty"] * 0.001
                              if fill["side"] == "sell" else 0.0)
            assert fill["fee"]["stamp_duty"] == pytest.approx(
                round(expected_stamp, 2), abs=1e-9)

    def test_sell_before_buy_cash_chain(self, reconciled):
        """同日卖+买：卖单在前（回笼资金供买单）——不出现资金不足拒单。"""
        pkg = reconciled["g7_lot_and_min_commission"]
        result = pkg["engine"]
        codes = {rejection.code.value for _order, rejection in result.rejections}
        assert "insufficient_cash" not in codes
        by_day: dict[str, list[str]] = {}
        for fill in result.fills:
            by_day.setdefault(fill.fill_date.to_ymd(), []).append(fill.side.value)
        both = [sides for sides in by_day.values()
                if "sell" in sides and "buy" in sides]
        assert both, "案例须含同日卖+买（资金链断言前提）"
        assert both[0][:2] == ["sell", "buy"], f"卖先买后失效：{both[0]}"


class TestGroup8Calendar:
    """G8：日历边界 —— 缺口与 W-FRI 周锚（两套独立实现互校）。"""

    def test_spring_festival_gap(self, reconciled):
        cal = reconciled["g8_calendar_spring_festival"]["case"]["assertions"]["calendar"]
        longest = max(g["calendar_days"] for g in cal["gaps"])
        assert longest >= 8, f"春节缺口应 ≥8 自然日，实测 {longest}"

    def test_engine_iterates_exactly_case_dates(self, reconciled):
        dates = reconciled["g8_calendar_spring_festival"]["case"]["data"]["dates"]
        snapshots = reconciled["g8_calendar_spring_festival"]["engine"].snapshots
        assert [s.date.to_ymd() for s in snapshots] == dates

    def test_week_anchors_match_qidian_implementation(self, reconciled):
        """构建器独立 W-FRI 实现 ≡ `btf.strategy.qidian.week_end_of`（互校）。"""
        from btf.strategy.qidian import week_end_of

        cal = reconciled["g8_calendar_spring_festival"]["case"]["assertions"]["calendar"]
        grouped: dict[str, list[str]] = {}
        for day in cal["dates"]:
            grouped.setdefault(week_end_of(day), []).append(day)
        assert grouped == cal["week_anchors"]


class TestGroup9HfqChain:
    """G9：复权链 —— 事件链收益 ≡ hfq 收益（adj_factor 独立复算）。"""

    def test_engine_return_matches_hfq(self, reconciled):
        pkg = reconciled["g9_dividend_chain_hfq"]
        hfq = pkg["case"]["assertions"]["hfq"]
        snaps = {s.date.to_ymd(): s for s in pkg["engine"].snapshots}
        frm, to = hfq["from"], hfq["to"]
        engine_return = snaps[to].total_value / snaps[frm].total_value - 1.0
        assert engine_return == pytest.approx(hfq["return"],
                                              abs=hfq["tolerance"]), (
            f"事件链 {engine_return:.6f} vs hfq {hfq['return']:.6f}")

    def test_window_has_two_dividends(self, reconciled):
        case = reconciled["g9_dividend_chain_hfq"]["case"]
        assert len(case["data"]["actions"]) >= 2, "案例须含 ≥2 次分红（复权链）"
        assert case["assertions"]["events"], "事件链须被触发（全程持仓）"


class TestGroup10Vpp:
    """G10：VPP 成交量参与率上限（v0.5 V5-6）—— 部分成交与量上限拒单。"""

    def test_partial_fill_at_volume_cap(self, reconciled):
        """成交数量 = floor(bar.vol × rate / lot) × lot（与委托量取小）。"""
        case = reconciled["g10_vpp_partial_fill"]["case"]
        rate = case["plan"]["execution"]["volume_participation"]
        order = case["plan"]["orders"][0]
        fill = case["assertions"]["fills"][0]
        bar = next(b for b in case["data"]["bars"] if b["date"] == fill["date"])
        expected = min(order["qty"], int(bar["vol"] * rate) // 100 * 100)
        assert fill["qty"] == expected < order["qty"], "部分成交（未到委托量）"

    def test_volume_cap_below_lot_rejects_entirely(self, reconciled):
        """vol × rate < 一手 → VOLUME_CAP 完全拒单（一笔不成交）。"""
        case = reconciled["g10_vpp_volume_cap_rejected"]["case"]
        assert case["assertions"]["rejected_counts"] == {"volume_cap": 1}
        assert case["assertions"]["fills"] == []

    def test_engine_partial_fills_match_refcalc(self, reconciled):
        """引擎部分成交与参考计算器逐笔一致（数量/价格/费用）。"""
        pkg = reconciled["g10_vpp_partial_fill"]
        engine_fill = pkg["engine"].fills[0]
        expected = pkg["case"]["assertions"]["fills"][0]
        assert engine_fill.qty == expected["qty"]
        assert engine_fill.price == pytest.approx(expected["price"], abs=TOL)
        assert engine_fill.qty < next(
            o.qty for o in pkg["engine"].orders if o.symbol == engine_fill.symbol)
