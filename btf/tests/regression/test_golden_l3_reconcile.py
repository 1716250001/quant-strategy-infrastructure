# -*- coding: utf-8 -*-
"""L3 端到端对账：独立参考计算器 vs 引擎（M2 任务 5.7；09 §14.3）。

对账容差：**1e-10**（09 §14.3）——记账/估值路径不得有任何口径漂移；
两侧共享同一外部条件（分段费率 tiered_v1、next_open 撮合、full 再平衡、
空风控链），差异只能归因于实现。

比对对象（逐项，非抽样）：
    ① 快照五字段：cash / market_value / total_value / daily_return /
       cumulative_return / drawdown —— 逐日
    ② 持仓数量 positions_qty —— 逐日（含送转后 qty 变化）
    ③ 成交 fills：date / side / qty / price / fee 四项
    ④ 指标：final_nav / total_return / max_drawdown / n_fills / total_fees /
       total_turnover（由引擎产物经 analytics.metrics 计算）
"""
from __future__ import annotations

import pytest

from tests.fixtures.golden import CASES_DIR, load_case, run_case

pytestmark = [pytest.mark.l3]

CASES = ["g1_cash_div_same_day", "g1_cash_div_ex_ne_pay", "g1_stk_div_mixed"]
TOL = 1e-10


def _require_cases() -> None:
    if not CASES_DIR.is_dir():
        pytest.skip(f"黄金集未构建: {CASES_DIR}（先跑 tools/build_golden.py）")


@pytest.fixture(scope="module")
def reconciled() -> dict[str, dict]:
    """每案例：(引擎结果, 参考断言)。"""
    _require_cases()
    out = {}
    for case_id in CASES:
        case = load_case(case_id)
        out[case_id] = {"engine": run_case(case), "case": case}
    return out


class TestSnapshotReconciliation:
    """① 快照逐字段对账（NAV 四式 + 持仓市值/现金）。"""

    @pytest.mark.parametrize("case_id", CASES)
    def test_day_count_matches(self, reconciled, case_id):
        eng, exp = reconciled[case_id]["engine"], reconciled[case_id]["case"]
        assert len(eng.snapshots) == len(exp["assertions"]["snapshots"])

    @pytest.mark.parametrize("case_id", CASES)
    def test_nav_fields_bitwise_close(self, reconciled, case_id):
        pkg = reconciled[case_id]
        exp_snaps = pkg["case"]["assertions"]["snapshots"]
        for got, want in zip(pkg["engine"].snapshots, exp_snaps, strict=True):
            assert got.date.to_ymd() == want["date"]
            for field in ("cash", "market_value", "total_value",
                          "daily_return", "cumulative_return", "drawdown"):
                assert getattr(got, field) == pytest.approx(
                    want[field], abs=TOL), f"{want['date']}.{field}"


class TestPositionReconciliation:
    """② 持仓数量逐日一致（含送转份额调整）。"""

    @pytest.mark.parametrize("case_id", CASES)
    def test_positions_qty(self, reconciled, case_id):
        pkg = reconciled[case_id]
        for got, want in zip(pkg["engine"].snapshots,
                             pkg["case"]["assertions"]["snapshots"],
                             strict=True):
            assert dict(got.positions_qty) == want["positions_qty"], want["date"]

    def test_stk_div_doubles_position(self, reconciled):
        """送转案例：ex 日 qty 按 1+stk_div 放大（G1 断言要点）。"""
        exp = reconciled["g1_stk_div_mixed"]["case"]["assertions"]
        event = next(e for e in exp["events"] if e["type"] == "ex")
        assert event["qty_after"] == round(event["qty_before"] * (1 + event["stk_div"]))


class TestFillReconciliation:
    """③ 成交逐笔一致（含费用分项）。"""

    @pytest.mark.parametrize("case_id", CASES)
    def test_fills_match(self, reconciled, case_id):
        eng_fills = reconciled[case_id]["engine"].fills
        exp_fills = reconciled[case_id]["case"]["assertions"]["fills"]
        assert len(eng_fills) == len(exp_fills)
        for got, want in zip(eng_fills, exp_fills, strict=True):
            assert got.fill_date.to_ymd() == want["date"]
            assert got.side.value == want["side"]
            assert got.qty == want["qty"]
            assert got.price == pytest.approx(want["price"], abs=TOL)
            for part in ("commission", "stamp_duty", "transfer_fee", "total"):
                assert getattr(got.fee, part) == pytest.approx(
                    want["fee"][part], abs=TOL), part

    @pytest.mark.parametrize("case_id", CASES)
    def test_no_rejections_in_golden_cases(self, reconciled, case_id):
        """G1 场景不应产生拒单（有钱有券、未触及涨跌停）。"""
        assert reconciled[case_id]["engine"].rejections == []


class TestMetricsReconciliation:
    """④ 指标：引擎产物 → analytics.metrics vs 参考计算器。"""

    @pytest.mark.parametrize("case_id", CASES)
    def test_nav_metrics(self, reconciled, case_id):
        from btf.analytics.metrics import compute_all

        pkg = reconciled[case_id]
        got = compute_all(pkg["engine"].snapshots, pkg["engine"].fills)
        want = pkg["case"]["assertions"]["metrics"]
        for key in ("final_nav", "total_return", "max_drawdown"):
            assert got[key] == pytest.approx(want[key], abs=TOL), key

    @pytest.mark.parametrize("case_id", CASES)
    def test_trade_metrics(self, reconciled, case_id):
        from btf.analytics.metrics import NFills, TotalFees, TotalTurnover

        pkg = reconciled[case_id]
        result, want = pkg["engine"], pkg["case"]["assertions"]["metrics"]
        assert NFills().compute(result.snapshots, result.fills, {}) == want["n_fills"]
        assert TotalFees().compute(
            result.snapshots, result.fills, {}) == pytest.approx(
                want["total_fees"], abs=TOL)
        assert TotalTurnover().compute(
            result.snapshots, result.fills, {}) == pytest.approx(
                want["total_turnover"], abs=TOL)


class TestInvariants:
    """G1 不变式：事件杏点到的 NAV 缺口在两侧一致。"""

    @pytest.mark.parametrize("case_id", CASES)
    def test_event_window_delta_zero(self, reconciled, case_id):
        """派息 ex→pay 窗口：登记数量到账，事件贡献 Δ=0。"""
        windows = reconciled[case_id]["case"]["assertions"]["invariants"]["windows"]
        for window in windows:
            assert window["window_event_delta"] == pytest.approx(0.0, abs=TOL)

    @pytest.mark.parametrize("case_id", CASES)
    def test_daily_cash_flow_conservation(self, reconciled, case_id):
        """现金式（G1 第一断言）：逐日 Δcash = 成交现金流 ± 派息到账。"""
        pkg = reconciled[case_id]
        result = pkg["engine"]
        events = pkg["case"]["assertions"]["events"]
        snaps = result.snapshots
        for i in range(1, len(snaps)):
            day = snaps[i].date.to_ymd()
            flow = 0.0
            for fill in result.fills:
                if fill.fill_date.to_ymd() != day:
                    continue
                flow += (-(fill.price * fill.qty + fill.fee.total)
                         if fill.side.value == "buy"
                         else (fill.price * fill.qty - fill.fee.total))
            flow += sum(e.get("cash_delta", 0.0) for e in events
                        if e["type"] == "pay" and e["date"] == day)
            assert snaps[i].cash - snaps[i - 1].cash == pytest.approx(
                flow, abs=TOL), day
