# -*- coding: utf-8 -*-
"""CL-2-H（19 号 §96.4）：ETF/LOF 公司行动 —— R32′ 分红 / R35′ 折算 /
R36′ 免印花税 / R26′ 指标口径（合成 parquet，主库无关）。

覆盖验收点：
  - R32′：去重（同日两行只入账一次）、`div_cash` 每份金额（**禁除 base_unit**）、
    `div_proc='实施'` 过滤、回退链 `pay_date→earpay_date→ex_date`、非交易日顺延；
  - R35′/R34′：双证据（`|R×(f_t÷f_{t−1})−1| < tol`）通过 ⇒ ok；扰动因子 ⇒
    fail-closed（不建模 + 披露）；因子缺失 ⇒ fail-closed；
  - R36′：ETF 卖出免印花税、股票侧逐位不变（N30）；
  - R26′：`win_rate` 在场日口径（空仓日不入分母）；无持仓信息 ⇒ 回落全样本
    （向后兼容）；`win_rate_trades` 逐笔辅助。
"""
from __future__ import annotations

import pathlib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.domain.orders import OrderSide
from btf.domain.types import TradingDate

DAYS = ["20240918", "20240919", "20240920", "20240923", "20240924",
        "20240925", "20240926", "20240927", "20240930"]


def _write_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "market_data"
    (root / "fund_daily").mkdir(parents=True)
    (root / "fund_adj").mkdir(parents=True)
    (root / "fund_div").mkdir(parents=True)
    (root / "metadata").mkdir(parents=True)

    # trade_cal（metadata 布局；2024-09-28/29 周末休市）
    pq.write_table(pa.table({
        "cal_date": [*DAYS, "20240928", "20240929"],
        "is_open": [1] * len(DAYS) + [0, 0],
    }), root / "metadata" / "trade_cal.parquet")

    # fund_daily：510310 折算（1.5990 → 3.2130，pre_close 3.1990 ⇒ 已吸收）
    #             512200 折算但**因子被扰动**（fail-closed 用例）
    rows = [("510310.SH", "20240920", 1.5990, 1.5990),
            ("510310.SH", "20240923", 3.1990, 3.2130)]
    rows += [("512200.SH", "20240920", 0.4410, 0.4410),
             ("512200.SH", "20240923", 1.1910, 1.1920)]
    pq.write_table(pa.table({
        "ts_code": [r[0] for r in rows],
        "trade_date": [r[1] for r in rows],
        "pre_close": [r[2] for r in rows],
        "open": [r[2] for r in rows], "high": [r[3] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
        "change": [0.0] * len(rows), "pct_chg": [0.0] * len(rows),
        "vol": [1.0] * len(rows), "amount": [1.0] * len(rows),
    }), root / "fund_daily" / "2024.parquet")

    # fund_adj：510310 = R 的倒数 0.49980（互校通过）；512200 = 0.30000（扰动）
    pq.write_table(pa.table({
        "ts_code": ["510310.SH", "510310.SH", "512200.SH", "512200.SH"],
        "trade_date": ["20240920", "20240923", "20240920", "20240923"],
        "adj_factor": [1.0, 0.49980, 1.0, 0.30000],
    }), root / "fund_adj" / "2024.parquet")

    # fund_div：去重（两行同 (ts_code, ex_date)）、回退（pay 空 → earpay）、
    #           非交易日顺延（pay=20240928 → 20240930）、预案过滤（div_proc）
    pq.write_table(pa.table({
        "ts_code": ["510880.SH"] * 5,
        "ex_date": ["20240919", "20240919", "20240924", "20240925",
                    "20240920"],
        "pay_date": ["20240923", "20240923", None, "20240928", "20240923"],
        "earpay_date": [None, None, "20240926", None, None],
        "record_date": ["20240918"] * 5,
        "ann_date": ["20240910"] * 5,
        "div_proc": ["实施", "实施", "实施", "实施", "预案"],
        "div_cash": [0.10, 0.10, 0.05, 0.02, 9.99],
        "base_unit": [23900.0, 723617.0, 1000.0, 1000.0, 1000.0],
    }), root / "fund_div" / "2024.parquet")
    return root


def _d(s: str) -> TradingDate:
    return TradingDate.from_ymd(s)


class TestFundDividendsR32:
    def test_dedup_fallback_rollover_and_per_share(self, tmp_path):
        from btf.data.etf_corp_actions import load_fund_dividends

        root = _write_root(tmp_path)
        events, notes = load_fund_dividends(
            root, ["510880.SH", "600000.SH"], _d("20240101"), _d("20241231"))
        # 去重 1（两行同 (ts_code, ex_date)）+ 预案过滤 0 + 有效 3
        assert len(events) == 3
        by_ex = {a.ex_date.to_ymd(): a for a in events}
        # ① div_cash 即每份金额（**禁除 base_unit**——0.10 不得变成 0.10/23900）
        assert by_ex["20240919"].cash_div_per_share == pytest.approx(0.10)
        assert by_ex["20240919"].pay_date.to_ymd() == "20240923"
        # ② 回退链 pay 缺 → earpay
        assert by_ex["20240924"].pay_date.to_ymd() == "20240926"
        # ③ 非交易日（2024-09-28 周六）⇒ 顺延至下一交易日 20240930
        assert by_ex["20240925"].pay_date.to_ymd() == "20240930"
        joined = "；".join(notes)
        assert "去重" in joined and "回退" in joined and "顺延" in joined

    def test_no_fund_symbols_returns_empty(self, tmp_path):
        from btf.data.etf_corp_actions import load_fund_dividends

        root = _write_root(tmp_path)
        events, notes = load_fund_dividends(
            root, ["600000.SH"], _d("20240101"), _d("20241231"))
        assert events == [] and notes == []


class TestScanConsolidationsR35:
    def test_dual_evidence_ok_and_fail_closed(self, tmp_path):
        from btf.data.etf_corp_actions import scan_consolidations

        root = _write_root(tmp_path)
        events, notes = scan_consolidations(
            root, ["510310.SH", "512200.SH"], _d("20240101"), _d("20241231"))
        assert len(events) == 2
        by_sym = {e["ts_code"]: e for e in events}
        ok = by_sym["510310.SH"]
        assert ok["ok"] is True
        assert ok["R"] == pytest.approx(2.000625, abs=1e-6)   # 锚点逐位
        assert ok["dev"] < 0.005                              # |R×ρ−1|
        bad = by_sym["512200.SH"]
        assert bad["ok"] is False                             # 因子扰动 ⇒ fail-closed
        assert bad["dev"] > 0.005
        assert any("fail-closed" in n for n in notes)

    def test_missing_factor_is_fail_closed(self, tmp_path):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from btf.data.etf_corp_actions import scan_consolidations

        root = _write_root(tmp_path)
        # 抹掉 512200 的因子行 ⇒ 证据缺失 ⇒ fail-closed（不得静默取其一）
        pq.write_table(pa.table({
            "ts_code": ["510310.SH", "510310.SH"],
            "trade_date": ["20240920", "20240923"],
            "adj_factor": [1.0, 0.49980],
        }), root / "fund_adj" / "2024.parquet")
        events, _notes = scan_consolidations(
            root, ["512200.SH"], _d("20240101"), _d("20241231"))
        assert events and events[0]["ok"] is False
        assert events[0]["evidence2"] is None

    def test_zero_factor_dirty_row_consumed_not_missing(self, tmp_path):
        """**R39′（N41，§97.6）**：`adj_factor=0.0` 脏行 —— `is not None`
        判定 ⇒ 视作**可读因子**（证据 2 不作 0 的倒数、偏差照算 =1.0），
        仍 **fail-closed** 但归因可辨（不是「因子缺失」静默归类）。"""
        import pyarrow as pa
        import pyarrow.parquet as pq
        from btf.data.etf_corp_actions import scan_consolidations

        root = _write_root(tmp_path)
        pq.write_table(pa.table({
            "ts_code": ["512200.SH", "512200.SH"],
            "trade_date": ["20240920", "20240923"],
            "adj_factor": [1.0, 0.0],           # 折算日因子脏行（0.0）
        }), root / "fund_adj" / "2024.parquet")
        events, _notes = scan_consolidations(
            root, ["512200.SH"], _d("20240101"), _d("20241231"))
        assert events and events[0]["ok"] is False        # 仍 fail-closed
        assert events[0]["dev"] == pytest.approx(1.0)     # 偏差可辨
        assert events[0]["evidence2"] is None             # 0 不作倒数

    def test_to_etf_corp_actions_ratio_is_inverse_r(self, tmp_path):
        from btf.data.etf_corp_actions import to_etf_corporate_actions

        root = _write_root(tmp_path)
        acts, _notes, summary = to_etf_corporate_actions(
            root, ["510310.SH"], _d("20240101"), _d("20241231"))
        cons = [a for a in acts if a.consolidation_ratio != 1.0]
        assert len(cons) == 1
        assert cons[0].consolidation_source == "fund_conv"
        # consolidation_ratio = 1/R（拆股 ⇒ 份额 ×2）
        assert cons[0].consolidation_ratio == pytest.approx(1 / 2.000625,
                                                            abs=1e-9)
        assert summary["n_conv"] == 1 and summary["n_unmodeled"] == 0


class TestCostFundExemptR36:
    def test_etf_stamp_duty_zero_stock_unchanged(self):
        from btf.execution.cost import AShareTieredFeeModel

        model = AShareTieredFeeModel()
        date = _d("20240102")          # 2023-08-28 后分段
        etf = model.fees("510310.SH", OrderSide.SELL, 100000, 3.0, date)
        stock = model.fees("600000.SH", OrderSide.SELL, 100000, 3.0, date)
        assert etf.stamp_duty == 0.0                     # ETF/LOF 免征
        assert stock.stamp_duty == pytest.approx(300000 * 0.0005, abs=0.01)
        # 其余科目不变（佣金含最低、过户费口径同现状）
        assert etf.commission == stock.commission or etf.commission == 5.0

    def test_etf_buy_unchanged(self):
        from btf.execution.cost import AShareTieredFeeModel

        model = AShareTieredFeeModel()
        fee = model.fees("510310.SH", OrderSide.BUY, 100000, 3.0,
                         _d("20240102"))
        assert fee.stamp_duty == 0.0


class TestWinRateInMarketR26:
    @staticmethod
    def _snap(tv: float, held: bool):
        from types import SimpleNamespace

        return SimpleNamespace(total_value=tv,
                               positions_qty=({"000001.SZ": 100} if held else {}))

    def test_in_market_denominator_excludes_empty_days(self):
        from btf.analytics.metrics import WinRate, build_nav_series

        # tv: 100 → 101(持) → 101(空) → 102(持) → 101(持)
        # 全样本日收益：+1%, 0%, +0.990%, −0.980% ⇒ 2/4 = 0.50
        # 在场日（第 1/3/4 日）：+1%, +0.990%, −0.980% ⇒ 2/3
        snaps = [self._snap(100.0, False), self._snap(101.0, True),
                 self._snap(101.0, False), self._snap(102.0, True),
                 self._snap(101.0, True)]
        series = build_nav_series(snaps)
        assert WinRate()._value(series, {}) == pytest.approx(2 / 3)

    def test_no_position_info_falls_back_to_all_days(self):
        from types import SimpleNamespace

        from btf.analytics.metrics import WinRate, build_nav_series

        # 老快照对象（无 positions_qty）⇒ 回落全样本：+1%, −1% ⇒ 1/2
        snaps = [SimpleNamespace(total_value=100.0),
                 SimpleNamespace(total_value=101.0),
                 SimpleNamespace(total_value=100.0)]
        series = build_nav_series(snaps)
        assert WinRate()._value(series, {}) == pytest.approx(0.5)

    def test_win_rate_trades_optional_metric(self):
        from types import SimpleNamespace

        from btf.analytics.metrics import OPTIONAL_METRIC_CLASSES, WinRateTrades, build_nav_series

        assert WinRateTrades in OPTIONAL_METRIC_CLASSES        # 不进默认 15 项
        snaps = [self._snap(100.0, True), self._snap(101.0, True)]
        series = build_nav_series(snaps)
        trades = (SimpleNamespace(close_fill=object(), pnl=10.0),
                  SimpleNamespace(close_fill=object(), pnl=-3.0),
                  SimpleNamespace(close_fill=None, pnl=0.0))
        from dataclasses import replace

        series = replace(series, trades=trades)
        assert WinRateTrades()._value(series, {}) == pytest.approx(0.5)


class TestEtfCoverageGuardR38:
    """**R38′（N40，§97.6）**：覆盖段**不得恒现**——宇宙内无 ETF/LOF ⇒ 不输出。"""

    @staticmethod
    def _res(divs=()):
        from types import SimpleNamespace

        return SimpleNamespace(dividend_events=list(divs), consolidations=[])

    @staticmethod
    def _feed(summary, notes=()):
        from types import SimpleNamespace

        return SimpleNamespace(etf_scan_summary=dict(summary),
                               etf_action_notes=list(notes))

    def test_pure_stock_or_unscanned_no_coverage_section(self):
        from btf.runtime.run_orchestrator import _etf_action_notes

        # ① 显式纯股票宇宙：n_symbols=0（v77 preRC 形态，`source=all`）
        feed = self._feed({"n_symbols": 0, "n_div": 0, "n_conv": 0,
                           "n_unmodeled": 0})
        assert _etf_action_notes(self._res(), feed) == []
        # ② 未做 ETF 扫描（summary 空——无显式宇宙/股票侧调用）
        assert _etf_action_notes(self._res(), self._feed({})) == []

    def test_etf_universe_keeps_coverage_section(self):
        from btf.runtime.run_orchestrator import _etf_action_notes

        feed = self._feed({"n_symbols": 3, "n_div": 0, "n_conv": 0,
                           "n_unmodeled": 0})
        notes = _etf_action_notes(self._res(), feed)
        assert len(notes) == 1 and "覆盖段" in notes[0]
        assert "命中分红 0 笔" in notes[0]          # ETF 宇宙：口径可见


def _write_div_cross_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """**R43′（N46）** 合成根：大额分红日同时满足折算幅度与双证据（旧逻辑会误建折算）。

    锚形（全库实测：184721.SZ 2008-04-03 分红率 70.47%）：前收 1.0 ⇒ 除息日
    `pre_close = close = 0.70`（jump −30% 且已吸收）；`fund_adj` 跳变 ＝ R 倒数
    （1/0.7）；`fund_div` 同日除息（div_cash 0.30）⇒ 交叉判为**分红**。
    """
    root = tmp_path / "market_data"
    for sub in ("fund_daily", "fund_adj", "fund_div", "metadata"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "cal_date": ["20240102", "20240103", "20240104"],
        "is_open": [1, 1, 1],
    }), root / "metadata" / "trade_cal.parquet")
    pq.write_table(pa.table({
        "ts_code": ["184721.SZ", "184721.SZ"],
        "trade_date": ["20240102", "20240103"],
        "pre_close": [1.0, 0.70], "open": [1.0, 0.70], "high": [1.0, 0.70],
        "low": [1.0, 0.70], "close": [1.0, 0.70], "change": [0.0, 0.0],
        "pct_chg": [0.0, 0.0], "vol": [1.0, 1.0], "amount": [1.0, 1.0],
    }), root / "fund_daily" / "2024.parquet")
    pq.write_table(pa.table({
        "ts_code": ["184721.SZ", "184721.SZ"],
        "trade_date": ["20240102", "20240103"],
        "adj_factor": [1.0, 1.0 / 0.70],       # 跳变 ＝ R 的倒数（dev ≈ 0）
    }), root / "fund_adj" / "2024.parquet")
    pq.write_table(pa.table({
        "ts_code": ["184721.SZ"], "ex_date": ["20240103"],
        "pay_date": ["20240104"], "earpay_date": [None],
        "record_date": ["20240102"], "ann_date": ["20231220"],
        "div_proc": ["实施"], "div_cash": [0.30], "base_unit": [1000.0],
    }), root / "fund_div" / "2024.parquet")
    return root


class TestDivConsCrossR43:
    """**R43′（N46，§98.4）**：折算候选与 `fund_div` 除息日交叉 ⇒ 判分红非折算。"""

    def test_large_dividend_not_misjudged_as_consolidation(self, tmp_path):
        from btf.data.etf_corp_actions import scan_consolidations, to_etf_corporate_actions

        root = _write_div_cross_root(tmp_path)
        events, notes = scan_consolidations(
            root, ["184721.SZ"], _d("20240101"), _d("20241231"))
        # 旧逻辑：|jump|=30%>12% ＋ dev≈0 ⇒ 误建折算；现：交叉 ⇒ 不计折算
        assert events == []
        assert any("除息日交叉剔除" in n for n in notes)
        acts, _notes, summary = to_etf_corporate_actions(
            root, ["184721.SZ"], _d("20240101"), _d("20241231"))
        assert summary["n_conv"] == 0 and summary["n_div"] == 1
        assert len(acts) == 1                              # 只入分红
        assert acts[0].cash_div_per_share == pytest.approx(0.30)
        assert acts[0].consolidation_ratio == 1.0

    def test_true_consolidation_without_dividend_still_modeled(self, tmp_path):
        """反向对照：无同日除息 ⇒ 折算照旧建模（510310 形）。"""
        from btf.data.etf_corp_actions import to_etf_corporate_actions

        root = _write_root(tmp_path)
        acts, _notes, summary = to_etf_corporate_actions(
            root, ["510310.SH"], _d("20240101"), _d("20241231"))
        cons = [a for a in acts if a.consolidation_ratio != 1.0]
        assert len(cons) == 1 and summary["n_conv"] == 1


class TestFundDivNonSilentR44:
    """**R44′（N47，§98.4）**：`fund_div` 缺失/无数据 ⇒ 显式披露（禁静默）。"""

    def test_missing_table_disclosed(self, tmp_path):
        from btf.data.etf_corp_actions import load_fund_dividends

        root = _write_root(tmp_path)
        (root / "fund_div" / "2024.parquet").unlink()      # 表缺失（快照漏带形）
        events, notes = load_fund_dividends(
            root, ["510880.SH"], _d("20240101"), _d("20241231"))
        assert events == []
        assert notes and "缺失" in notes[0] and "禁静默" in notes[0]

    def test_present_but_empty_window_disclosed(self, tmp_path):
        from btf.data.etf_corp_actions import load_fund_dividends

        root = _write_root(tmp_path)                       # 表在（2024 数据）
        events, notes = load_fund_dividends(
            root, ["510880.SH"], _d("20250101"), _d("20251231"))
        assert events == []
        assert any("0 条记录" in n for n in notes)

    def test_missing_directory_disclosed(self, tmp_path, monkeypatch):
        """**R48′（N52，§99.5）**：**目录缺失**（非「文件删空」）⇒ 走同一告警分支。

        `peek_columns` 在表目录缺失时抛 `ReaderError`（`_list_year_files`
        拒绝静默）——原实现未捕获 ⇒ 告警分支走不到（N52 缺口）。
        """
        import shutil

        from btf.data.etf_corp_actions import load_fund_dividends

        root = _write_root(tmp_path)
        shutil.rmtree(root / "fund_div")                   # 整目录缺失（freeze 漏带形）
        assert not (root / "fund_div").exists()
        monkeypatch.setattr(
            "btf.data.etf_corp_actions._FUND_DIV_CACHE", {}, raising=False)
        events, notes = load_fund_dividends(
            root, ["510880.SH"], _d("20240101"), _d("20241231"))
        assert events == []
        assert notes and "缺失" in notes[0] and "禁静默" in notes[0]
        assert "目录缺失" in notes[0]


class TestScanNoCalendarR47:
    """**R47′（N51，§99.5）**：折算扫描**不依赖** `trade_cal`/`fund_div` 存在性。"""

    def test_a_dividend_rows_without_calendar(self, tmp_path):
        """A：有 `fund_div` 行 ＋ **无 `trade_cal`** ⇒ 扫描不崩（原抛未捕获异常）。"""
        import shutil

        from btf.data.etf_corp_actions import load_fund_dividends, scan_consolidations

        root = _write_div_cross_root(tmp_path)
        shutil.rmtree(root / "metadata")                   # 无日历
        events, notes = scan_consolidations(
            root, ["184721.SZ"], _d("20240101"), _d("20241231"))
        assert events == []                                # 交叉剔除仍生效
        assert any("除息日交叉剔除" in n for n in notes)
        # 装载本身：need_calendar=False ⇒ 不读日历、不抛
        divs, _ = load_fund_dividends(root, ["184721.SZ"], _d("20240101"),
                                      _d("20241231"), need_calendar=False)
        assert len(divs) == 1

    def test_b_missing_fund_div_dir_without_calendar(self, tmp_path):
        """B：**`fund_div` 目录缺失** ＋ 无日历 ⇒ R44′ 告警、扫描继续（原 ReaderError）。"""
        import shutil

        from btf.data.etf_corp_actions import scan_consolidations

        root = _write_root(tmp_path)
        shutil.rmtree(root / "fund_div")
        shutil.rmtree(root / "metadata")
        events, notes = scan_consolidations(
            root, ["510310.SH"], _d("20240101"), _d("20241231"))
        assert len(events) == 1                            # 折算照常给出
        assert any("缺失" in n and "禁静默" in n for n in notes)


class TestFractionAndOrderR45:
    """**R45′（N48/N49，§98.4）**：碎份口径披露＋同日双事件顺序定义。"""

    def test_fraction_dropped_recorded_and_disclosed(self):
        from btf.domain.action import CorporateAction
        from btf.portfolio.portfolio import Portfolio, Position

        p = Portfolio(cash=0.0)
        p.positions["510310.SH"] = Position(symbol="510310.SH", qty=777700,
                                            available_qty=777700, avg_cost=3.0)
        p._last_close["510310.SH"] = 3.0
        p.apply_ex_date(CorporateAction(
            symbol="510310.SH", ex_date=_d("20240923"), pay_date=None,
            record_date=None, cash_div_per_share=0.0, stk_div_per_share=0.0,
            ann_date=None, consolidation_ratio=0.499843798812871,
            consolidation_source="fund_conv"))
        ev = p.consolidations[0]
        assert ev["qty_before"] == 777700 and ev["qty_after"] == 388728
        # 777700 × 0.499843798812871 = 388728.52… ⇒ 丢弃 **0.5223 份**（全精度）
        #（审查方按 6 位近似 0.499844 估 ≈0.68~0.7 份——同量级；口径以全精度为准）
        ratio = 0.499843798812871
        assert ev["qty_fraction_dropped"] == pytest.approx(
            777700 * ratio - 388728, abs=1e-6)       # 事件值 round(…, 6)
        assert 0.0 < ev["qty_fraction_dropped"] < 1.0

    def test_same_day_order_consolidation_before_dividend(self):
        from btf.data.etf_corp_actions import action_order_key
        from btf.domain.action import CorporateAction

        div = CorporateAction(symbol="510310.SH", ex_date=_d("20240923"),
                              pay_date=None, record_date=None,
                              cash_div_per_share=0.1, stk_div_per_share=0.0,
                              ann_date=None)
        cons = CorporateAction(symbol="510310.SH", ex_date=_d("20240923"),
                               pay_date=None, record_date=None,
                               cash_div_per_share=0.0, stk_div_per_share=0.0,
                               ann_date=None, consolidation_ratio=0.5)
        # ADR-20 第 ⑩ 条：先折算后分红（现金按折算后份额计）
        assert sorted([div, cons], key=action_order_key) == [cons, div]


class TestCrossFailSafeR51:
    """**R51′（N55，§100.4）**：交叉防线 fail-safe——失能上报＋「有行全滤」披露。"""

    def test_e2_table_missing_sets_cross_disabled(self, tmp_path):
        """E2：`fund_div` 目录缺失 ⇒ `cross_disabled=True`（70% 分红形按幅度保留）。"""
        import shutil

        from btf.data.etf_corp_actions import scan_consolidations

        root = _write_div_cross_root(tmp_path)
        shutil.rmtree(root / "fund_div")
        state: dict = {}
        events, _notes = scan_consolidations(
            root, ["184721.SZ"], _d("20240101"), _d("20241231"),
            cross_state=state)
        assert len(events) == 1                        # 交叉不可用 ⇒ 按幅度保留
        assert events[0]["R"] == pytest.approx(0.70)
        assert state["disabled"] is True               # 失能上报（⇒ degraded/退出码 2）
        assert state["stats"]["table_missing"] is True

    def test_e3_rows_all_filtered_disclosed(self, tmp_path):
        """E3：行在但被 `div_proc` 全滤 ⇒ **三元计数披露**（原零告警静默）。"""
        from btf.data.etf_corp_actions import scan_consolidations

        root = _write_div_cross_root(tmp_path)
        pq.write_table(pa.table({                     # 除息行改「预案」⇒ 被过滤
            "ts_code": ["184721.SZ"], "ex_date": ["20240103"],
            "pay_date": ["20240104"], "earpay_date": [None],
            "record_date": ["20240102"], "ann_date": ["20231220"],
            "div_proc": ["预案"], "div_cash": [0.30], "base_unit": [1000.0],
        }), root / "fund_div" / "2024.parquet")
        state: dict = {}
        events, notes = scan_consolidations(
            root, ["184721.SZ"], _d("20240101"), _d("20241231"),
            cross_state=state)
        assert len(events) == 1                        # 交叉无输入 ⇒ 按幅度保留
        assert state["disabled"] is False              # 表在（非失能）
        assert state["stats"]["rows_raw"] == 1
        assert state["stats"]["n_proc_filtered"] == 1
        assert state["stats"]["n_events"] == 0
        joined = "；".join(notes)
        assert "有行但过滤后为空" in joined            # E3 告警（三元计数）
        assert "最终入账 0 笔" in joined


class TestOrderKeyScopeR53:
    """**R53′（N57，§100.6）**：排序键限 ETF 作用域＋`or 1.0` → `is not None`。"""

    @staticmethod
    def _act(sym: str, *, cash: float = 0.0, cons: float | None = None):
        from btf.domain.action import CorporateAction

        kw = {}
        if cons is not None:
            kw = {"consolidation_ratio": cons,
                  "consolidation_source": "fund_conv"}
        return CorporateAction(symbol=sym, ex_date=_d("20240923"),
                               pay_date=None, record_date=None,
                               cash_div_per_share=cash, stk_div_per_share=0.0,
                               ann_date=None, **kw)

    def test_scope_limited_to_etf(self):
        from btf.data.etf_corp_actions import action_order_key

        # 股票侧：第三键恒 1（**不扩权**——同键 ⇒ 保持输入序＝R49′ 前逐位一致）
        s_div, s_cons = self._act("600000.SH", cash=0.1), \
            self._act("600000.SH", cons=0.5)
        assert action_order_key(s_div) == action_order_key(s_cons)
        assert action_order_key(s_cons)[2] == 1
        # ETF 侧：先折算后分红
        e_div, e_cons = self._act("510310.SH", cash=0.1), \
            self._act("510310.SH", cons=0.5)
        assert sorted([e_div, e_cons], key=action_order_key) == [e_cons, e_div]

    def test_zero_ratio_invalid_not_modeled(self):
        """**R54′（N59 裁决 (a)，§102.1）**：`0.0` ⇒ **无效值、不建模**（份额不变）、
        禁走保底清零；排序键归「非折算类」（不参与先折算后分红）。"""
        from btf.data.etf_corp_actions import action_order_key
        from btf.portfolio.portfolio import Portfolio, Position

        assert action_order_key(self._act("510310.SH", cons=0.0))[2] == 1
        p = Portfolio(cash=0.0)
        p.positions["510310.SH"] = Position(symbol="510310.SH", qty=100,
                                            available_qty=100, avg_cost=1.0)
        p._last_close["510310.SH"] = 1.0
        p.apply_ex_date(self._act("510310.SH", cons=0.0))
        assert p.consolidations == []                  # 无事件（不建模）
        pos = p.positions["510310.SH"]
        assert pos.qty == 100 and pos.avg_cost == pytest.approx(1.0)   # 份额不变
        assert p.dividend_events == []


class TestVoidedBannerR40:
    """**R40′（N42，§97.6）**：作废横幅轻量 MD 渲染（粗体/行内代码）。"""

    def test_md_lite_renders_bold_and_code(self):
        from btf.viz.report import _md_lite

        out = _md_lite("漏计约 **36.98 万 = 本金 36.98%**；先执行 "
                       "`bt data scan-consolidation` 确认")
        assert "<strong>36.98 万 = 本金 36.98%</strong>" in out
        assert "<code>bt data scan-consolidation</code>" in out
        assert "**" not in out and "`" not in out

    def test_md_lite_escapes_html_before_markup(self):
        from btf.viz.report import _md_lite

        out = _md_lite("<script>alert(1)</script> & `x` **y**")
        assert "<script>" not in out and "&lt;script&gt;" in out
        assert "<code>x</code>" in out and "<strong>y</strong>" in out
