# -*- coding: utf-8 -*-
"""MM-6（19 号 §137 B）多基准：Schema 边界 / 归一助手 / 主基准独占 metrics。

口径（派单原文）：
    ① `report.benchmark` 允许 `string | array[string]`（**1–3 个**，元素
       pattern 不变，`additionalProperties: false` 保持）；
    ② **首个＝主基准**——只有主基准的相对指标进 `metrics.json`（键名与数值
       口径逐字不变，R1 digest 不受影响）；
    ③ 其余基准只进披露区（`report.benchmarks[]`，随 manifest 落盘）；
    ④ `noise_baseline` 用主基准。

本文件钉住 ①②③ 的**配置层与分发层**（真实数据端到端见
`tests/system/test_cli_benchmark_e2e.py::TestCliMultiBenchmark`——铁律新 13：
系统级验收必须经 CLI 真入口，不得以 `persist=False` 充当）。
"""
from __future__ import annotations

import pytest
from btf.config.loader import benchmark_symbols, load_config
from btf.config.validation import ConfigError, validate

#: 最小合法配置（默认层补齐 execution/risk/report 等；同 test_config 口径）
MINIMAL = """\
schema_version: "backtest.v1"
run:
  strategy: "tests.fixtures.buyhold:ConfigBuyHold"
  period: {start: "2015-01-05", end: "2015-01-09"}
data:
  feed: "memory"
"""


def _cfg(benchmark):
    """最小配置 + 指定 `report.benchmark`（不经默认层，避免缺省值干扰）。"""
    cfg = load_config(MINIMAL)
    cfg["report"] = {"benchmark": benchmark}
    return cfg


class TestSchemaMultiBenchmark:
    """Schema：单值向后兼容 + 数组 1–3 个 + 元素 pattern/去重不放松。"""

    def test_single_string_still_valid(self):
        """既有单基准配置**零改动**仍通过（向后兼容的第一条证据）。"""
        validate(_cfg("000300.SH"))

    def test_array_of_two_valid(self):
        validate(_cfg(["000300.SH", "000001.SH"]))

    def test_array_of_three_valid(self):
        validate(_cfg(["000300.SH", "000001.SH", "399006.SZ"]))

    def test_four_rejected(self):
        """上限 3（派单明确 1–3 个）——第 4 个即拒，不静默截断。"""
        with pytest.raises(ConfigError) as exc:
            validate(_cfg(["000300.SH", "000001.SH", "399006.SZ", "000905.SH"]))
        assert "report.benchmark" in str(exc.value)

    @pytest.mark.parametrize("bad", [[], ["000300"], ["000300.SH", "000300"]])
    def test_empty_or_bad_element_rejected(self, bad):
        """空数组 / 缺交易所后缀 ⇒ 拒（元素 pattern 不因数组化而放松）。"""
        with pytest.raises(ConfigError):
            validate(_cfg(bad))

    def test_duplicate_element_rejected(self):
        """重复元素 ⇒ 拒（uniqueItems）：否则同一基准会算两遍、披露两行。"""
        with pytest.raises(ConfigError):
            validate(_cfg(["000300.SH", "000300.SH"]))


class TestBenchmarkSymbols:
    """归一助手：单值/数组 → 元组（**首个＝主基准**，顺序即优先级）。"""

    def test_absent_returns_empty(self):
        assert benchmark_symbols({}) == ()
        assert benchmark_symbols(None) == ()

    def test_falsy_returns_empty(self):
        """空串/空数组/None ⇒ 空元组（RC-09：无基准对照，不伪造）。"""
        assert benchmark_symbols({"benchmark": ""}) == ()
        assert benchmark_symbols({"benchmark": []}) == ()
        assert benchmark_symbols({"benchmark": None}) == ()

    def test_string_kept_as_single(self):
        assert benchmark_symbols({"benchmark": "000300.SH"}) == ("000300.SH",)

    def test_array_order_preserved(self):
        """**顺序即语义**：首个是主基准，不得排序/去重改写。"""
        assert benchmark_symbols(
            {"benchmark": ["000001.SH", "000300.SH"]}
        ) == ("000001.SH", "000300.SH")


class TestPrimaryOnlyInMetrics:
    """分发层：主基准进 metrics 返回值，其余**只**进披露明细。"""

    def _rt(self, monkeypatch, benchmark):
        from btf.runtime import BTFRuntime

        rt = BTFRuntime()
        rt.config = {"report": {"benchmark": benchmark}, "analysis": {}}
        calls: list[str] = []

        def fake_compute(symbol, snapshots, *, analysis=None):
            calls.append(symbol)
            # 每个基准给不同数值，便于证明"主基准那份才是 metrics"
            return {"excess_return": float(len(calls)), "beta": 1.0}

        monkeypatch.setattr(rt, "_compute_relative", fake_compute)
        return rt, calls

    def test_single_benchmark_metrics_and_one_detail_row(self, monkeypatch):
        rt, calls = self._rt(monkeypatch, "000300.SH")
        metrics = rt._benchmark_metrics(["snap-1", "snap-2"])
        assert calls == ["000300.SH"]
        assert metrics == {"excess_return": 1.0, "beta": 1.0}
        assert rt.benchmark_symbol == "000300.SH"
        assert len(rt.benchmark_details) == 1
        assert rt.benchmark_details[0]["primary"] is True

    def test_multi_benchmark_metrics_equals_primary_only(self, monkeypatch):
        """②的核心断言：metrics 与"只配主基准"时**逐键相同**（不多不少）。"""
        rt, calls = self._rt(monkeypatch, ["000300.SH", "000001.SH"])
        metrics = rt._benchmark_metrics(["snap-1", "snap-2"])
        assert calls == ["000300.SH", "000001.SH"], "两个基准都要算（披露需要）"
        assert metrics == {"excess_return": 1.0, "beta": 1.0}, (
            "第二基准的数值（excess_return=2.0）不得进 metrics（P0-NEW-7）")
        symbols = [row["symbol"] for row in rt.benchmark_details]
        assert symbols == ["000300.SH", "000001.SH"]
        assert [row["primary"] for row in rt.benchmark_details] == [True, False]
        assert rt.benchmark_details[1]["excess_return"] == 2.0

    def test_no_benchmark_no_details(self, monkeypatch):
        """未配基准 ⇒ 空 metrics 且**不产出**明细（RC-09 不留假行）。"""
        rt, calls = self._rt(monkeypatch, None)
        assert rt._benchmark_metrics(["snap-1"]) == {}
        assert calls == []
        assert rt.benchmark_details == ()
