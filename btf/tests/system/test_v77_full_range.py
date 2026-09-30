# -*- coding: utf-8 -*-
"""L5 系统：V7.7 策略**真实数据全区间计算链验证**（19 号审查 R1-2 验收）。

⚠️ **验证边界（AA-2 / 铁律新 13 留痕）**：本文件 = **计算链验证，非系统级 e2e**。
    两处 `rt.run(persist=False)`（`:56`、`:123`）**不落盘** → 覆盖不到 RunStore
    写入、`metrics.json` 序列化、报告生成、`bt verify` 复核——**正是把
    P0-NEW-7「带基准的 CLI run 必崩」藏了五轮的那条路径**（第五种逃逸模式：
    测试与生产执行路径不一致）。
    ⇒ **系统级 e2e（真入口 `bt run` → 落盘 → `bt report` → `bt verify`）见
    `tests/system/test_cli_benchmark_e2e.py`**；本文件因其"计算正确性 + 量级/
    分布锚点"价值保留（真实主库、≈900 交易日、LIQ 分布断言），但**不得**被
    当作系统级验收引用。若需在此增验落盘链，请改为 `persist=True` + 断言产物。

背景（报告的三大证据之一）：`V77Strategy` 此前**从未在真实全区间跑通**——
仅单测覆盖且注入假 `liq_fn`，故其性能缺陷（LIQ 逐日重读整年表，十年
760s）不可能被暴露。本测试是"该路径计算链可用"的常驻证据（R1-1 预计算
+ R1-2 跑通）。

红线：区间 2023-01-01~2026-09-23（≈900 交易日）**完整跑完**并产出指标；
LIQ 走预计算序列（`_liq_series` 注入非空）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.runtime import BTFRuntime

pytestmark = [pytest.mark.l5]

ROOT = Path(MARKET_DATA_DIR)
MIRROR = Path(r"D:\量化策略\赤潮\rules_mirror_v77.json")

requires = pytest.mark.skipif(
    not (ROOT / "daily").is_dir() or not MIRROR.is_file(),
    reason=f"主库或规则镜像不可用（{ROOT} / {MIRROR}）")

CONFIG = {
    "schema_version": "backtest.v1",
    "run": {
        "strategy": "btf.strategy.v77:V77Strategy",
        "params": {"top_k": 10},
        "universe": {"source": "all"},
        "period": {"start": "2023-01-01", "end": "2026-09-23"},
        "initial_cash": 1_000_000,
    },
    "data": {"feed": "tushare_parquet"},
    "execution": {"handler": "next_open", "cost_model": "flat_rate",
                  "rebalancer": "full"},
    "risk": {"rules": [{"name": "tradability"}, {"name": "max_weight"},
                       {"name": "cash_check"}]},
    "rules": {"source": "mirror_json", "path": str(MIRROR)},
    "report": {"benchmark": "000300.SH"},        # R2.5：基准对比一并验收
}


@requires
def test_v77_full_range_engine_rerun():
    """V77 真实全区间：装配（含 LIQ 预计算注入）→ 回测 → 指标产出。

    **计算链验证（非系统级）**——`persist=False` 不落盘；系统级 e2e 见
    `tests/system/test_cli_benchmark_e2e.py`（AA-2 / 铁律新 13）。
    """
    rt = BTFRuntime().load_config(dict(CONFIG)).build()
    assert rt.rules_provider.rules_version().startswith("v7.7@sha256:")
    assert rt.strategy._liq_series is not None, "LIQ 预计算未注入（R1-1）"
    assert len(rt.strategy._liq_series) > 800

    result = rt.run(persist=False)
    assert result.n_days > 800, f"交易日数异常: {result.n_days}"
    assert len(result.snapshots) == result.n_days
    # 有持仓与成交（策略不是空转；也验证风控链未把全部订单拒掉）
    assert len(result.fills) > 100, f"成交过少: {len(result.fills)}"
    last = result.snapshots[-1]
    assert last.total_value > 0
    assert rt.strategy.last_state is not None


@requires
def test_liq_state_distribution_sane():
    """X-3：LIQ 状态分布断言（防 RECOVERY 语义回归；19 号 §17.3 / §18.2）。

    为何必须断言**分布**而非单点：X-1 的 RECOVERY 自我维持 bug 通过了全部
    四条既有防线（等值测试同错互证 / 单测伪造输入 / 机检只比阈值 / 端到端
    只断言 fills>100）。单点与等值断言**结构上**抓不到它，只有分布能。

    锚点值（904 日窗口实测留痕）：
        · X-1 修复前：RECOVERY 343 (37.9%)、平均仓位上限 **33.90%**
        · X-1 修复后（2026-09-28）：NORMAL 862 | CRISIS 7 | RECOVERY 19 |
          WATCH 16；RECOVERY 段 8 / 最长 3 日；平均仓位上限 **48.24%**
        · **Z-6 入闸确认期落地后（2026-09-29）**：NORMAL 845 | **CRISIS 24** |
          RECOVERY 21 | WATCH 14；**确认期日 15**；RECOVERY 段 7 / 最长 3 日；
          平均仓位上限 **47.28%**（确认期按 0 成的保守化影响 −0.96pp）

    Z-6 新增断言（入闸确认期**结构性**守卫，非仅看占比）：
        ① 存在「status=CRISIS 而 raw=NORMAL」的**确认期日**（>0）；
        ② 每个 RECOVERY 段之前**恰有 ≥3 日 CRISIS**（连续平静 3 日才入闸）——
           若实现退回「危机次日即 RECOVERY」，本断言立即失败。
    """
    from btf.data.liq import POSITION_CAPS

    rt = BTFRuntime().load_config(dict(CONFIG)).build()
    series = rt.strategy._liq_series
    assert series, "LIQ 序列为空（X-3 断言前提）"
    days = sorted(series)

    counts: dict[str, int] = {}
    runs: list[int] = []
    starts: list[int] = []
    cur = 0
    for i, day in enumerate(days):
        status = series[day].status
        counts[status] = counts.get(status, 0) + 1
        if status == "RECOVERY":
            if cur == 0:
                starts.append(i)
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)

    n = len(days)
    recovery_share = counts.get("RECOVERY", 0) / n
    normal_share = counts.get("NORMAL", 0) / n
    avg_cap = sum(POSITION_CAPS[series[d].status] for d in days) / n
    max_run = max(runs, default=0)
    confirm_days = sum(1 for d in days
                       if series[d].status == "CRISIS"
                       and series[d].raw == "NORMAL")
    print(f"\nLIQ 分布: {counts} | RECOVERY 段 {len(runs)} 个 最长 {max_run} 日 "
          f"| 确认期日 {confirm_days} | 平均仓位上限 {avg_cap:.2f}%")

    assert normal_share > 0.90, f"NORMAL 占比异常偏低: {normal_share:.1%}"
    assert recovery_share < 0.05, (
        f"RECOVERY 占比 {recovery_share:.1%}——RECOVERY 自我维持回归（X-1）")
    # 窗口内再遇 CRISIS 可顺延，故留 2x 余量（修复后实测 3 日，修复前 182 日）
    assert max_run <= 6, f"RECOVERY 连续 {max_run} 日——窗口有界性回归（X-1）"
    assert avg_cap > 45.0, (
        f"平均仓位上限 {avg_cap:.2f}% 被系统性低估（修复前 33.90%）")
    # ── Z-6：入闸确认期结构性守卫（占比之外，防"悄悄退回次日恢复"）──
    if counts.get("CRISIS", 0) > 0:
        assert confirm_days > 0, (
            "区间内有 CRISIS 却零确认期日——Z-6 入闸确认期未生效")
        for i in starts:
            assert i >= 3, f"RECOVERY 段起点索引 {i} < 3（无确认期）"
            before = [series[days[i - k]].status for k in (1, 2, 3)]
            assert before == ["CRISIS"] * 3, (
                f"{days[i]} 进入 RECOVERY 前 3 日状态 {before}——"
                f"入闸确认期缺失（Z-6 回归）")


@requires
def test_benchmark_metrics_wired_end_to_end():
    """R2.5 验收：配置 `report.benchmark` 后相对指标真正并入 metrics。

    ⚠️ 名中的 "end_to_end" 指**计算链**（装配→回测→指标），**非**系统级
    端到端：此处 `persist=False`，不覆盖落盘/摘要/报告/复核（AA-2 留痕）。
    真入口系统级验收见 `tests/system/test_cli_benchmark_e2e.py`。
    """
    from btf.analytics.metrics import compute_all

    rt = BTFRuntime().load_config(dict(CONFIG)).build()
    result = rt.run(persist=False)
    metrics = rt._benchmark_metrics(result)
    assert metrics, "基准相对指标为空（report.benchmark 未接线？）"
    # P0-NEW-7（19 号 §24.3）：`benchmark_symbol` 是**元信息**，不得混入数值
    # 指标集（否则 CLI 落盘路径 `save_metrics` 的 float() 必崩）；
    # 元信息改挂 runtime.benchmark_symbol，数值项留在 metrics。
    assert "benchmark_symbol" not in metrics
    assert rt.benchmark_symbol == "000300.SH"
    for key in ("benchmark_total_return", "excess_return",
                "tracking_error", "information_ratio", "beta"):
        assert key in metrics, key
    base = compute_all(result.snapshots, result.fills, {})
    assert base["n_trading_days"] == result.n_days
