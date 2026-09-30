# -*- coding: utf-8 -*-
"""稳健性工具的**基准端点**回归（P1-NEW-9；19 号 §54.3 / §55）。

**缺陷复盘**：`_bench_series` 曾用 `start <= str(day) <= end` 做**字典序**比较——
`day` 来自 parquet（紧凑 `"20260923"`）、`start/end` 为 `"2026-09-23"`（连字符）。
第 5 字符 `'0'(0x30) > '-'(0x2D)` ⇒ **end 所在年份的全部行被静默过滤** ⇒ 基准终点
被截到 `2025-12-31`（EE-1 声称的 `+19.09%` 正是这么来的）。

**防线（铁律新 15：必须可被证伪）**：
    ① 同一区间，**连字符入参 == 紧凑入参**（同一序列、逐位相等）；
    ② **终点年份被包含**（`end="2026-09-23"` ⇒ 含 `20260923`、不含 `20260924`）；
    ③ **端点留痕**：`_excess_ir` 返回 `strat_*/bench_*` 四端点，端点不一致可核；
    ④ **变异测试**：把 `_ymd` 打残为恒等函数（模拟未规范化）⇒ ② 必红。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import run_v77_robustness as tool  # noqa: E402


@pytest.fixture()
def bench(tmp_path, monkeypatch):
    """合成 `index_daily`（by_year 布局），并把工具的数据根指向它。"""
    idx = tmp_path / "index_daily"
    idx.mkdir(parents=True)
    rows = {
        2025: [("000300.SH", "20251230", 100.0),
               ("000300.SH", "20251231", 102.0)],
        2026: [("000300.SH", "20260102", 103.0),
               ("000300.SH", "20260923", 119.09),
               ("000300.SH", "20260924", 120.0)],
    }
    for year, data in rows.items():
        pq.write_table(pa.table({
            "ts_code": [d[0] for d in data],
            "trade_date": [d[1] for d in data],
            "close": [d[2] for d in data],
        }), idx / f"{year}.parquet")

    from btf.config import paths

    monkeypatch.setattr(paths, "MARKET_DATA_DIR", tmp_path)
    return tool


def _series(tool_mod, start: str, end: str) -> dict[str, float]:
    return tool_mod._bench_series("000300.SH", start, end)


class TestBenchSeriesRange:
    def test_dashed_equals_compact(self, bench):
        """① 入参格式不影响结果（连字符 vs 紧凑，逐位相等）。"""
        a = _series(bench, "2025-12-30", "2026-09-23")
        b = _series(bench, "20251230", "20260923")
        assert a == b
        assert a, "序列为空（数据根钩子未生效？）"

    def test_end_year_included(self, bench):
        """② **终点年份必须被包含**——P1-NEW-9 的直接回归断言。"""
        s = _series(bench, "2025-12-30", "2026-09-23")
        assert "20260923" in s, f"终点年份被过滤（P1-NEW-9 回归）：{sorted(s)}"
        assert "20260924" not in s, "越界日（晚于 end）不得进入序列"

    def test_start_boundary_exclusive_before_start(self, bench):
        s = _series(bench, "2026-01-02", "2026-09-23")
        assert "20251231" not in s
        assert "20260102" in s


class TestEndpointDisclosure:
    def test_excess_ir_returns_endpoints(self, bench):
        """③ 端点留痕：四端点齐备且两侧对齐（本案缺的正是这个可核性）。"""
        nav = [("20251230", 1.0), ("20251231", 1.01), ("20260102", 1.02),
               ("20260923", 1.20)]
        out = bench._excess_ir(nav, _series(bench, "2025-12-30", "2026-09-23"))
        assert out["strat_first"] == out["bench_first"] == "20251230"
        assert out["strat_last"] == out["bench_last"] == "20260923"
        assert out["benchmark_return"] == pytest.approx(119.09 / 100.0 - 1.0)
        assert out["excess_return"] == pytest.approx(0.20 - 0.1909)

    def test_excess_ir_exposes_misalignment(self, bench):
        """端点不一致时**如实暴露**（不再是静默错值）。"""
        nav = [("20251230", 1.0), ("20260923", 1.20)]
        out = bench._excess_ir(nav, {"20251230": 100.0, "20251231": 102.0})
        assert out["bench_last"] == "20251231"
        assert out["strat_last"] == "20260923"      # 一眼可见不一致


class TestMutation:
    """④ 变异测试：打残 `_ymd`（恒等）⇒ 断言② 必红。"""

    def test_identity_ymd_reproduces_defect(self, bench, monkeypatch):
        monkeypatch.setattr(bench, "_ymd", lambda v: v)      # 打残：不规范化
        s = _series(bench, "2025-12-30", "2026-09-23")
        assert "20260923" not in s, (
            "打残后未复现端点截断 ⇒ 断言② 无牙齿（伪防线）")
