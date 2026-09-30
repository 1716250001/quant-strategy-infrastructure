# -*- coding: utf-8 -*-
"""L5 基准 B5：单 run HTML 报告生成 < 5s（09 §14.5；M2 5.10 归档）。

场景：合成 5 年日线快照（~1220 交易日）+ 200 笔交易 → 六图 + 单文件
自包含 HTML（plotly.js 内联），端到端生成耗时 < 5s。
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from btf.experiment.manifest import RunManifest
from btf.experiment.store import RunBundle, SnapshotRecord
from btf.viz.report import Assumptions, ReportBuilder

pytestmark = [pytest.mark.l5]

B5_LIMIT_SECONDS = 5.0
N_DAYS = 1220          # ≈5 年交易日


def _snapshots() -> list[SnapshotRecord]:
    out, peak = [], 0.0
    day = date(2020, 1, 1)
    nav = 1_000_000.0
    for i in range(N_DAYS):
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        nav *= 1.0002 if i % 7 else 0.999
        peak = max(peak, nav)
        out.append(SnapshotRecord(
            date=day.strftime("%Y%m%d"), cash=nav * 0.1,
            market_value=nav * 0.9, total_value=nav,
            daily_return=0.0 if not out else nav / out[-1].total_value - 1.0,
            cumulative_return=nav / 1_000_000.0 - 1.0,
            drawdown=nav / peak - 1.0,
            weights={"000001.SZ": 0.5, "600000.SH": 0.4, "@CASH": 0.1},
            positions_qty={"000001.SZ": 1000, "600000.SH": 800}))
    return out


def _bundle() -> RunBundle:
    snaps = _snapshots()
    trades = [{
        "trade_id": f"T{i:06d}", "symbol": "000001.SZ", "qty": 100,
        "pnl": 12.5, "holding_days": 20, "tag": None,
        "open_fill": {"fill_id": f"F{i}", "order_id": "O1",
                      "symbol": "000001.SZ", "side": "buy", "qty": 100,
                      "price": 10.0,
                      "fee": {"commission": 5.0, "stamp_duty": 0.0,
                              "transfer_fee": 0.01, "total": 5.01},
                      "fill_date": "20200102", "fill_timing": "open"},
        "close_fill": None,
    } for i in range(200)]
    return RunBundle(
        manifest=RunManifest.skeleton(
            {"run": {"period": {"start": "2020-01-01", "end": "2024-12-31"}},
             "data": {"feed": "memory"}}, run_id="b5_bench_000000"),
        metrics={f"metric_{i}": float(i) for i in range(15)},
        snapshots=snaps, trades=trades, fills=[], rejections=[],
    )


def test_b5_report_generation():
    bundle = _bundle()
    assumptions = Assumptions(
        matching="next_open", slippage="none",
        fee_segments=({"effective_from": None, "stamp_duty_sell": 0.001,
                       "transfer_fee_rate": 0.0006},),
        rules_version="v7.7@sha256:ab12")
    t0 = time.perf_counter()
    html = ReportBuilder(bundle, assumptions).build()
    elapsed = time.perf_counter() - t0
    print(f"\nB5-report: {len(bundle.snapshots)} 快照 / "
          f"{len(html):,} 字节 | {elapsed:.2f}s（预算 {B5_LIMIT_SECONDS}s）")
    assert 'id="assumptions"' in html
    assert elapsed < B5_LIMIT_SECONDS, f"B5 超标：{elapsed:.2f}s"
