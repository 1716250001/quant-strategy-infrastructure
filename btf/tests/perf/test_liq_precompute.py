# -*- coding: utf-8 -*-
"""L5 性能/等值：LIQ 序列预计算（19 号架构审查 R1-1 / P0-1）。

红线（报告 §15.2，红→绿计时断言闭环）：
    - 十年全区间预计算 **< 5 s**（基线：`state_of` 逐日实测 760 s，≥150x）
    - 语义等值：对抽样日 `precompute[ymd] == state_of(ymd, prev_statuses=历史)`
      逐字段相等（口径唯一实现 `liq._finalize`，手段差异由本测试钉住）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.liq import (
    CRISIS,
    NORMAL,
    RECOVERY,
    WATCH,
    precompute_liq_series,
    state_of,
)

pytestmark = [pytest.mark.l5]

ROOT = Path(MARKET_DATA_DIR)
TEN_YEARS = ("20160101", "20251231")
#: **软目标**（P0-1 原始目标：760s 逐日 → <5s；超出仅打印告警，不判失败）
DECADE_BUDGET_SECONDS = 5.0
#: **硬门槛**（§40.5 D-8，同 B2/B1/B4 族；实测区间 3.6–5.3s）：负载/机器态波动
#: 下不产生假失败；1.6x 余量仍足以捕获数量级回归。**真正的回归守卫**是机器无关
#: 的两条——语义等值（`test_matches_state_of_on_sample`）与 IO 精确性
#: （`test_precompute_reuses_store_cache` 断言 `loads ≤ 40`）。
LIQ_HARD_LIMIT_SECONDS = 8.0

#: 规则源口径阈值 = **镜像 JSON「L0_LIQ」真值**（X-10，19 号 §17.4.5）
#:
#: 原测试自带副本（sh_crisis −3.0 / down_crisis 100 / ratio_crisis 20.0 …）
#: 与生产镜像（−5.0 / 800 / 10.0）**数值完全不同**：同字段而不同值 →
#: 性能与状态覆盖的代表性失真（RECOVERY/CRISIS 触发频率差一个量级）。
#: 今改为真值；镜像可用时由 `test_thresholds_match_mirror` 钉住不再漂移。
THRESHOLDS = {
    "sh_crisis": -5.0, "sh_watch": -3.0,
    "small_crisis": -6.0, "small_watch": -4.0,
    "down_crisis": 800, "down_watch": 300,
    "ratio_crisis": 10.0, "ratio_watch": 5.0,
}

#: 生产镜像（可用时与 THRESHOLDS 对账；不可用时跳过而非放行）
MIRROR = Path(r"D:\量化策略\赤潮\rules_mirror_v77.json")


def test_thresholds_match_mirror():
    """X-10：测试阈值 == 镜像 JSON「L0_LIQ」真值（防止再次静默漂移）。"""
    if not MIRROR.is_file():
        pytest.skip(f"规则镜像不可用: {MIRROR}")
    payload = json.loads(MIRROR.read_text(encoding="utf-8"))
    rules = payload.get("rules") or {}

    def find(node):
        if isinstance(node, dict):
            if "sh_crisis" in node:
                return node
            for value in node.values():
                got = find(value)
                if got:
                    return got
        return None

    liq = find(rules)
    assert liq, "镜像缺 L0_LIQ"
    assert {k: float(v) for k, v in liq.items() if k in THRESHOLDS} == {
        k: float(v) for k, v in THRESHOLDS.items()}

requires_mainlib = pytest.mark.skipif(
    not (ROOT / "daily").is_dir(), reason=f"主库不可用: {ROOT}")


@requires_mainlib
def test_decade_precompute_within_budget():
    """十年区间预计算：**软目标 5s / 硬门槛 8s**（红→绿：基线 760s 逐日口径）。

    门槛纪律（§40.5 D-8；同 B2/B1/B4 族）：墙钟门槛 + 窄余量（原 5.0s vs 实测
    3.6–5.0s）⇒ 负载/机器态一变即**假失败**。今：软目标 = P0-1 原始目标
    （超出只打印告警），硬门槛 = 8.0s（当前实测 4.7–5.3s 的 ~1.6x）。
    **真正的回归守卫是机器无关的两条**：① 语义等值（`test_matches_state_of_on_sample`）；
    ② IO 精确性（`test_precompute_reuses_store_cache` 断言 `loads ≤ 40`）。
    """
    t0 = time.perf_counter()
    series = precompute_liq_series(*TEN_YEARS, THRESHOLDS)
    elapsed = time.perf_counter() - t0
    soft = elapsed <= DECADE_BUDGET_SECONDS
    print(f"\nLIQ 十年预计算: {elapsed:.2f}s"
          f"（软目标 {DECADE_BUDGET_SECONDS}s{'✓' if soft else '✗ 超软目标（负载？）'}｜"
          f"硬门槛 {LIQ_HARD_LIMIT_SECONDS}s）"
          f"| {len(series)} 交易日 | 加速 ≈{760.0 / elapsed:.0f}x"
          f"（vs 历史逐日基线 760s）")
    assert len(series) > 2300
    assert elapsed < LIQ_HARD_LIMIT_SECONDS, (
        f"预计算超标：{elapsed:.2f}s ≥ 硬门槛 {LIQ_HARD_LIMIT_SECONDS}s（软目标 "
        f"{DECADE_BUDGET_SECONDS}s 的 1.6x）——回查 core 缓存/免排序/谓词下推是否退化")


@requires_mainlib
def test_decade_recovery_bounded():
    """X-1 十年尺度验收：RECOVERY ≈3 日/次（修复前 27.1% / 最长 182 日）。

    与 `tests/system/test_v77_full_range.py::test_liq_state_distribution_sane`
    互为双锚点（区间 vs 十年）；两者都只可能在**真阈值**下成立（X-10）。
    """
    series = precompute_liq_series(*TEN_YEARS, THRESHOLDS)
    days = sorted(series)
    runs, cur = [], 0
    recovery = 0
    for day in days:
        status = series[day].status
        if status == RECOVERY:
            recovery += 1
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    share = recovery / len(days)
    print(f"\nRECOVERY {recovery}/{len(days)} ({share:.1%}) | "
          f"段数 {len(runs)} | 最长 {max(runs, default=0)} 日")
    assert share < 0.05, f"RECOVERY 占比 {share:.1%}（自我维持回归？X-1）"
    assert max(runs, default=0) <= 6, f"RECOVERY 连续 {max(runs)} 日（窗口有界性）"


@requires_mainlib
def test_matches_state_of_on_sample():
    """语义等值：抽样日逐字段 == `state_of`（含 RECOVERY 序列上下文）。"""
    series = precompute_liq_series(*TEN_YEARS, THRESHOLDS)
    days = sorted(series)
    # 抽样：每状态取前若干 + 均匀抽样（覆盖边界）
    by_status: dict[str, list[str]] = {}
    for day in days:
        by_status.setdefault(series[day].status, []).append(day)
    sample: list[str] = []
    for status in (CRISIS, WATCH, RECOVERY, NORMAL):
        sample.extend(by_status.get(status, [])[:6])
    sample.extend(days[::60])
    sample = sorted(set(sample))

    sample_set = set(sample)
    prev = None                    # 单步递推：用前一日**预计算结果**作 prev
    mismatches = []
    for day in days:
        if day in sample_set:
            ref = state_of(day, THRESHOLDS, prev=prev)
            got = series[day]
            if (ref.status, ref.raw, ref.phase, ref.phase_days, ref.sh_pct,
                    ref.small_pct, ref.down_cnt, ref.down_ratio,
                    ref.triggers) != (
                    got.status, got.raw, got.phase, got.phase_days, got.sh_pct,
                    got.small_pct, got.down_cnt, got.down_ratio,
                    got.triggers):
                mismatches.append(day)
        prev = series[day]                         # 与预计算同源推进状态
    assert sample, "抽样为空（区间无交易日？）"
    assert not mismatches, f"抽样 {len(sample)} 日中有 {len(mismatches)} 日不等值"


@requires_mainlib
def test_precompute_reuses_store_cache():
    """装载计数：每表每年**至多一次**装载；容量足够时二次调用零 IO
    （报告 §15.3；容量不足会触发 FIFO 淘汰重装载——本测试显式给定容量）。"""
    from btf.data.core import YearTableStore

    store = YearTableStore(max_entries=48)       # ≥ 10 年 × 3 表 + 日索引年
    precompute_liq_series(*TEN_YEARS, THRESHOLDS, store=store)
    first = store.stats()["loads"]
    # 10 年 ×（stk_limit + daily 免排序 + index_daily 过滤 + days 日索引）= 40
    assert first <= 40, f"装载次数异常: {store.stats()}"
    precompute_liq_series(*TEN_YEARS, THRESHOLDS, store=store)
    assert store.stats()["loads"] == first, "二次调用未命中缓存"
