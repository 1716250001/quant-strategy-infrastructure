# -*- coding: utf-8 -*-
"""L2 集成：**P14（19 号 §172 C）** CC-7 送转一致性检查器（只报不阻断）。

- `002608.SZ@20161228`（10 送 13.87、`adj_factor` 恒 2.604）**必被
  `phantom_shares` 捕获**（G#6）。
- **只报不阻断**：同一报告即使 CC-7 有 findings，`QualityReport.ok` 仍为
  True（`strict` 不因它失败），`summary_line` 明标「（不阻断）」。
- 干净切片（20 标的 × 2024）CC-7 PASS（无恒空：`checked_rows` 为事件数）。
"""
from __future__ import annotations

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.quality import run_checks

pytestmark = [pytest.mark.l2]

requires_mainlib = pytest.mark.skipif(
    not (MARKET_DATA_DIR / "adj_factor").is_dir(),
    reason=f"主库不可用: {MARKET_DATA_DIR}")


def _cc7(report):
    return next(r for r in report.rules if r.rule == "CC-7")


@requires_mainlib
def test_phantom_shares_captured():
    """`002608.SZ@20161228` ⇒ `phantom_shares`，且不阻断整份报告。"""
    rep = run_checks(MARKET_DATA_DIR, "20161201", "20170131",
                     symbols={"002608.SZ"})
    rule = _cc7(rep)
    hits = [f for f in rule.findings if "phantom_shares" in f.detail
            or "全程未跳变" in f.detail]
    assert hits, f"CC-7 未捕获 002608：{[f.detail for f in rule.findings]}"
    assert any("002608.SZ@20161228" in f.samples for f in hits)
    assert rep.ok is True            # 只报不阻断
    assert "不阻断" in rep.summary_line()


@requires_mainlib
def test_clean_slice_passes_with_events_checked():
    """干净切片（20 标的 × 2024）：CC-7 PASS 且 checked_rows > 0。"""
    syms = {"000001.SZ", "600519.SH", "600276.SH", "000002.SZ"}
    rep = run_checks(MARKET_DATA_DIR, "20240101", "20241231", symbols=syms)
    rule = _cc7(rep)
    assert rule.ok
    assert rule.checked_rows > 0     # 600276 5 年窗内有送转事件


@requires_mainlib
def test_cc7_not_blocking_when_findings():
    """有 findings 时 `ok` 仍 True（阻断项与只报项分离）。"""
    rep = run_checks(MARKET_DATA_DIR, "20161201", "20170131",
                     symbols={"002608.SZ"})
    assert not _cc7(rep).ok
    assert rep.ok
