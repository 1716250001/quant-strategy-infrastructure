# -*- coding: utf-8 -*-
"""奇点战法复跑对账（M3 任务 6.3；验收 B1/B2/B3）。

B1 信号一致率 100%：同一批**真实周线**下，btf 纯 Python 复刻（W-FRI 聚合 +
KDJ + CCI + 信号）与真源实现（`代码/indicators.py::calc_kdj_standard` +
真源 `aggregate_to_weekly` / `calc_cci` / `check_signal_detail`）逐项一致。
B2 名单验证：真源注释分组 14/23/4/2；23 只「双回测一致优秀」逐只解析为
T+1 股票型 ETF（可转债组为 T+0，单独断言）+ 数据可用性证据。
B3 ETF 撮合 T+0：`domain.infer_instrument` 推断子类 → `rule_for` → 撮合器
同日往返（511180/511380 可卖；股票型 ETF 510300 拒 T_PLUS_1）。

主库不可用 → skip（不静默通过）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR, QIDIAN_REF
from btf.data import parquet_reader as pr
from btf.domain.market import Bar, TradingState
from btf.domain.orders import Order, OrderSide, OrderType
from btf.domain.types import TradingDate, infer_instrument, rule_for
from btf.execution.handler import NextOpenHandler
from btf.portfolio.portfolio import Portfolio
from btf.strategy.qidian import (
    calc_cci,
    calc_kdj,
    load_reference,
    params_of,
    pool_groups,
    pool_of,
    rules_of,
    signal_of,
    weekly_bars,
)

pytestmark = [pytest.mark.l4]

ROOT = Path(MARKET_DATA_DIR)
START, END = "20240101", "20260923"
DAY = TradingDate.from_ymd("20260923")
_DAILY_COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "vol"]


@pytest.fixture(scope="module")
def reference():
    return load_reference()


@pytest.fixture(scope="module")
def indicators():
    """真源指标库（仅 numpy 依赖）：KDJ 参考实现。"""
    path = QIDIAN_REF.parent.parent / "indicators.py"
    if not path.is_file():
        pytest.skip(f"真源指标库缺失：{path}")
    spec = importlib.util.spec_from_file_location("btf_qidian_indicators", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skip_if_no_data() -> None:
    if not (ROOT / "fund_daily").is_dir() or not (ROOT / "index_daily").is_dir():
        pytest.skip("主库 fund_daily/index_daily 不可用")


def _rows_by_code(table: str, codes: set[str]) -> dict[str, list[dict]]:
    """一次区间读 → 按标的归并（升序）。

    仅保留 OHLC 齐全行：部分指数（如 H30315.CSI / 950041.CSI）源端只提供
    close（真源 docstring 已注明），无 OHLC 无法参与 KDJ/CCI 对账。
    """
    data = pr.read_range(table, ROOT, START, END, columns=_DAILY_COLS)
    out: dict[str, list[dict]] = {}
    for row in data.to_pylist():
        code = row["ts_code"]
        if code not in codes:
            continue
        if any(row.get(key) is None for key in ("open", "high", "low", "close")):
            continue
        out.setdefault(code, []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: str(r["trade_date"]))
    return out


class _BarView:
    """真源行 → btf `weekly_bars` 消费的最小 Bar 视图。"""

    __slots__ = ("close", "date", "high", "low", "open", "volume")

    def __init__(self, row: dict) -> None:
        self.date = str(row["trade_date"])
        self.open = float(row["open"])
        self.high = float(row["high"])
        self.low = float(row["low"])
        self.close = float(row["close"])
        self.volume = float(row.get("vol") or 0.0)


@pytest.fixture(scope="module")
def pairs(reference) -> list[tuple[str, str]]:
    """(ETF, 指数) 对（真源池 + 首个有数据的配对指数）。"""
    _skip_if_no_data()
    pool = pool_of(reference)
    index_codes = {idx["code"] for info in pool.values()
                   for idx in info.get("indices") or []}
    etf_rows = _rows_by_code("fund_daily", set(pool))
    index_rows = _rows_by_code("index_daily", index_codes)
    out: list[tuple[str, str]] = []
    for code, info in pool.items():
        if code not in etf_rows:
            continue
        for entry in info.get("indices") or []:
            if entry["code"] in index_rows:
                out.append((code, entry["code"]))
                break
    return out


class TestB1SignalEquivalence:
    """B1：btf 复刻 vs 真源实现，同输入逐项一致（信号一致率 100%）。"""

    def test_bars_indicators_and_signal_identical(
            self, reference, indicators, pairs):
        pd = pytest.importorskip("pandas")

        assert pairs, "无任何 ETF/指数 可用数据（对账空集不通过）"
        params = params_of(reference)
        rules = rules_of(reference)
        etf_rows = _rows_by_code("fund_daily", {c for c, _ in pairs})
        index_rows = _rows_by_code("index_daily", {c for _, c in pairs})

        compared = 0
        for etf_code, index_code in pairs:
            for tag, rows in (("etf", etf_rows[etf_code]),
                              ("index", index_rows[index_code])):
                btf_weekly = weekly_bars([_BarView(r) for r in rows])
                ref_weekly = reference.aggregate_to_weekly(pd.DataFrame(rows))
                # ① 周线聚合一致（周末日 + OHLC）
                assert [w.week_end for w in btf_weekly] == \
                    [str(d) for d in ref_weekly["week_end_date"]], \
                    f"{etf_code}/{index_code} {tag} 周末日不一致"
                for i, week in enumerate(btf_weekly):
                    assert week.open == pytest.approx(
                        float(ref_weekly["open"][i]), abs=1e-12)
                    assert week.high == pytest.approx(
                        float(ref_weekly["high"][i]), abs=1e-12)
                    assert week.low == pytest.approx(
                        float(ref_weekly["low"][i]), abs=1e-12)
                    assert week.close == pytest.approx(
                        float(ref_weekly["close"][i]), abs=1e-12)
                high = [w.high for w in btf_weekly]
                low = [w.low for w in btf_weekly]
                close = [w.close for w in btf_weekly]
                # ② KDJ 一致（真源 calc_kdj_standard）
                ref_k, ref_d, ref_j = indicators.calc_kdj_standard(
                    pd.Series(high).to_numpy(), pd.Series(low).to_numpy(),
                    pd.Series(close).to_numpy(),
                    n=params.kdj_n, m1=params.kdj_m1, m2=params.kdj_m2)
                btf_k, btf_d, btf_j = calc_kdj(high, low, close, params)
                assert (btf_k, btf_d, btf_j) == pytest.approx(
                    (float(ref_k), float(ref_d), float(ref_j)), abs=1e-9), \
                    f"{etf_code}/{index_code} {tag} KDJ 不一致"
                # ③ CCI 一致（真源 calc_cci）
                ref_cci = reference.calc_cci(
                    pd.Series(high).to_numpy(), pd.Series(low).to_numpy(),
                    pd.Series(close).to_numpy())
                btf_cci = calc_cci(high, low, close, params)
                assert btf_cci == pytest.approx(float(ref_cci), abs=1e-9), \
                    f"{etf_code}/{index_code} {tag} CCI 不一致"
                # ④ 信号一致（真源 check_signal_detail；买入优先）
                ref_signal = reference.check_signal_detail(
                    float(ref_j), float(ref_cci))["signal"]
                assert signal_of(btf_j, btf_cci, rules) == ref_signal, \
                    f"{etf_code}/{index_code} {tag} 信号不一致"
                compared += 1
        print(f"\nB1 对账完成：{len(pairs)} 对 ETF/指数，逐序列项 {compared}")
        assert compared == len(pairs) * 2


class TestB2PoolRoster:
    """B2：23 只「双回测一致优秀」名单验证。"""

    def test_group_counts(self, reference):
        groups = pool_groups()
        assert (len(groups["recommended"]), len(groups["dual_backtest"]),
                len(groups["industry_scan"]), len(groups["convertible_bond"])) \
            == (14, 23, 4, 2)
        assert len(pool_of(reference)) == 43

    def test_dual_backtest_members_are_t1_stock_etfs(self, reference):
        """23 只名单逐只：配对指数齐全，且解析为股票型 ETF（T+1）。"""
        pool = pool_of(reference)
        for code in pool_groups()["dual_backtest"]:
            info = pool[code]
            assert info.get("indices"), f"{code} 缺配对指数"
            assert info["recommended"] is False
            assert rule_for(infer_instrument(code, info["name"])).t_plus == 1, \
                f"{code} 应为 T+1 股票型 ETF"

    def test_dual_backtest_data_availability(self, reference):
        """数据可用性证据（不达标不静默：打印缺失清单）。"""
        _skip_if_no_data()
        pool = pool_of(reference)
        codes = set(pool_groups()["dual_backtest"])
        index_codes = {idx["code"] for code in codes
                       for idx in pool[code].get("indices") or []}
        etf_rows = _rows_by_code("fund_daily", codes)
        index_rows = _rows_by_code("index_daily", index_codes)
        with_data = [c for c in sorted(codes) if c in etf_rows and any(
            idx["code"] in index_rows for idx in pool[c]["indices"])]
        print(f"\nB2 数据可用：{len(with_data)}/{len(codes)} 只"
              f"（缺数据：{sorted(codes - set(with_data))[:8]}）")
        assert len(with_data) >= 20, "23 只名单数据可用率过低（<20）"


class TestB3EtfTPlus0Matching:
    """B3：ETF 撮合含 T+0 断言（511180/511380）。"""

    @staticmethod
    def _order(symbol: str, side: OrderSide, qty: int = 100) -> Order:
        return Order(order_id=f"O-{side.value}", symbol=symbol, side=side,
                     order_type=OrderType.MARKET, qty=qty, limit_price=None,
                     created_at=DAY)

    @staticmethod
    def _section(symbol: str, price: float) -> tuple[dict, dict]:
        bar = Bar(symbol=symbol, date=DAY, open=price, high=price, low=price,
                  close=price, volume=1e6, amount=price * 1e6, pre_close=price)
        state = TradingState(symbol=symbol, date=DAY, limit_up_price=None,
                             limit_down_price=None, is_suspended=False,
                             is_st=False, is_delisted=False,
                             is_limit_up=False, is_limit_down=False)
        return {symbol: bar}, {symbol: state}

    def test_inference_gives_t0_for_convertible_bond(self, reference):
        pool = pool_of(reference)
        for code in pool_groups()["convertible_bond"]:
            assert rule_for(infer_instrument(code, pool[code]["name"])).t_plus == 0

    def test_same_day_roundtrip_allowed_for_t0_etf(self, reference):
        """511180：当日买入立即可卖（子类 BOND → T+0，E2 双维规则）。"""
        pool = pool_of(reference)
        code = pool_groups()["convertible_bond"][0]
        handler = NextOpenHandler(
            instruments={code: infer_instrument(code, pool[code]["name"])})
        portfolio = Portfolio(cash=100_000.0)
        data, states = self._section(code, 10.0)
        handler.execute([self._order(code, OrderSide.BUY)], DAY, data, states,
                        portfolio)
        fills, rejects = handler.execute(
            [self._order(code, OrderSide.SELL)], DAY, data, states, portfolio)
        assert len(fills) == 1 and not rejects, "T+0 ETF 同日应可卖"
        assert fills[0].side is OrderSide.SELL

    def test_stock_etf_still_t1(self):
        """对照：股票型 ETF（510300）当日买入当日卖 → T_PLUS_1 拒单。"""
        symbol = "510300.SH"
        assert rule_for(infer_instrument(symbol, "沪深300ETF")).t_plus == 1
        handler = NextOpenHandler(
            instruments={symbol: infer_instrument(symbol, "沪深300ETF")})
        portfolio = Portfolio(cash=100_000.0)
        data, states = self._section(symbol, 4.0)
        handler.execute([self._order(symbol, OrderSide.BUY)], DAY, data, states,
                        portfolio)
        fills, rejects = handler.execute(
            [self._order(symbol, OrderSide.SELL)], DAY, data, states, portfolio)
        assert not fills and len(rejects) == 1
        assert rejects[0][1].code.value == "t_plus_1"

    def test_money_etf_t0(self):
        """货币 ETF（511880 名称口径）同样 T+0（子类 MONEY）。"""
        symbol = "511880.SH"
        assert rule_for(infer_instrument(symbol, "银华日利货币ETF")).t_plus == 0
