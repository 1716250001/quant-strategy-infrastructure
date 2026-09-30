# -*- coding: utf-8 -*-
"""v7.7 规则策略单测（M3 任务 6.2；L2 硬筛 + L0-LIQ 状态机）。

单测用**合成输入**（classify 纯函数 + 注入 screen_fn/liq_fn），真实主库
对账归集成测试（tests/integration/test_v77_align.py，A1/A2）。
"""
from __future__ import annotations

import pytest
from btf.data.liq import (
    CONFIRM,
    CRISIS,
    NORMAL,
    POSITION_CAPS,
    RECOVERY,
    WATCH,
    LiqState,
    classify,
    state_of,
)
from btf.domain.types import TradingDate
from btf.strategy.v77 import V77Strategy

pytestmark = [pytest.mark.l1]

TH = {"sh_crisis": -5.0, "sh_watch": -3.0, "small_crisis": -6.0,
      "small_watch": -4.0, "down_crisis": 800, "down_watch": 300,
      "ratio_crisis": 10.0, "ratio_watch": 5.0}


class FakeRules:
    """最小 RulesProvider（镜像 JSON 两组规则）。"""

    def __init__(self, l2=None, liq=None):
        self._rules = {"L2_filter": l2 or {"pe_max": 30.0, "pb_max": 3.0,
                                           "roe_min": 5.0, "dv_min": 1.0},
                       "L0_LIQ": liq or dict(TH)}

    def rules_version(self) -> str:
        return "v7.7@sha256:test"

    def get(self, rule_id: str):
        return dict(self._rules[rule_id])


class FakeCtx:
    """策略上下文替身（只记录 submit_target）。"""

    def __init__(self):
        self.targets: list = []

    def submit_target(self, target):
        self.targets.append(target)


def _day(ymd: str) -> TradingDate:
    return TradingDate.from_ymd(ymd)


class TestLiqClassify:
    def test_crisis_by_down_count(self):
        status, triggers = classify(sh_pct=-1.0, small_pct=-1.0, down_cnt=900,
                                    down_ratio=2.0, thresholds=TH)
        assert status == CRISIS and triggers == ("down_cnt≥800",)

    def test_crisis_by_small_cap(self):
        status, _ = classify(sh_pct=-1.0, small_pct=-6.5, down_cnt=10,
                             down_ratio=1.0, thresholds=TH)
        assert status == CRISIS

    def test_crisis_by_sh_and_ratio(self):
        status, _ = classify(sh_pct=-5.2, small_pct=-2.0, down_cnt=100,
                             down_ratio=11.0, thresholds=TH)
        assert status == CRISIS

    def test_sh_alone_is_watch(self):
        """上证 ≤-5% 但占比 <10% → WATCH（非 CRISIS）。"""
        status, triggers = classify(sh_pct=-5.5, small_pct=0.0, down_cnt=10,
                                    down_ratio=2.0, thresholds=TH)
        assert status == WATCH and any("sh≤-3.0" in t for t in triggers)

    def test_watch_by_ratio(self):
        status, _ = classify(sh_pct=-1.0, small_pct=-1.0, down_cnt=50,
                             down_ratio=5.5, thresholds=TH)
        assert status == WATCH

    def test_normal(self):
        status, triggers = classify(sh_pct=0.5, small_pct=1.0, down_cnt=5,
                                    down_ratio=0.1, thresholds=TH)
        assert status == NORMAL and triggers == ()

    def test_missing_small_skips_branch(self):
        """中证2000 基期前缺失 → 小盘分支 fail-closed（不误判 CRISIS）。"""
        status, _ = classify(sh_pct=-7.0, small_pct=None, down_cnt=0,
                             down_ratio=0.0, thresholds=TH)
        assert status == WATCH          # 仅上证触发 WATCH


class TestRecoverySequence:
    """确认期 + RECOVERY 序列语义（X-1/X-2 ／ **Z-6**；19 号 §17.3 / §18.2 / §26.5）。

    证据纪律（X-12 铁律首次适用）：序列依赖逻辑一律**序列推进**测试——
    逐日推进、当日**原始判定**回灌次日输入，不用手工构造的历史
    （旧测试的手工序列「CRISIS 次日的 NORMAL」在真实推进下不可能出现，
    曾用不存在的输入验出错误结论，见 §17.3.5）。
    """

    def test_confirm_then_recovery_then_normal(self, monkeypatch):
        """12 日真实推进：`CRISIS →（确认期 CRISIS×3）→ RECOVERY×3 → NORMAL`。

        规则源（`rules/single-source.md` L75，**Z-6 澄清 2026-09-29**）：
        「原始 CRISIS 解除后须**连续 3 个交易日**非 CRISIS 且非 WATCH 方进入
        RECOVERY；**确认期内状态按 CRISIS 约束（0 成）**」+「RECOVERY 3 日 → NORMAL」。
        X-1 修复前实测为 `RECOVERY × 12`（自我维持、永不退出）。
        """
        days = [f"202401{d:02d}" for d in range(2, 14)]     # 12 个交易日
        crisis_day = days[0]
        monkeypatch.setattr(
            "btf.data.liq.index_pct",
            lambda _sym, ymd, root=None: -9.0 if ymd == crisis_day else 1.0)
        monkeypatch.setattr(
            "btf.data.liq.limit_stats", lambda _ymd, root=None: (0, 100, 0.0))

        prev = None                       # 单步递推（Z-6：机器记忆随状态回传）
        statuses: list[str] = []
        raws: list[str] = []
        phases: list[str] = []
        for day in days:
            state = state_of(day, TH, prev=prev)
            statuses.append(state.status)
            raws.append(state.raw)
            phases.append(f"{state.phase}:{state.phase_days}")
            prev = state

        assert raws == [CRISIS] + [NORMAL] * 11        # 原始判定序列
        assert statuses[0] == CRISIS
        # 入闸确认期：平静日 1/2 日仍按 CRISIS（0 成）；第 3 个平静日**入闸**
        assert statuses[1:3] == [CRISIS] * 2, statuses
        assert statuses[3:6] == [RECOVERY] * 3, statuses  # 恢复期（≤1 成）
        assert statuses[6:] == [NORMAL] * 6, statuses
        assert RECOVERY not in statuses[6:], "RECOVERY 自我维持（X-1 回归）"
        # 相位证据链（铁律新 16：披露字段不得恒空）
        assert phases[:7] == ["CONFIRM:0", "CONFIRM:1", "CONFIRM:2",
                              "RECOVERY:0", "RECOVERY:1", "RECOVERY:2",
                              "IDLE:0"], phases

    def test_watch_interrupts_confirmation(self, monkeypatch):
        """确认期内出现 WATCH → 计时归零重计；**当日恒按 CRISIS（0 成）**。

        规则源要求「连续 3 日无危机**且无 WATCH**」——WATCH 打断后须重新确认；
        当日最终态与原始判定**不同**（`raw=WATCH` / `status=CRISIS`），两字段
        同时披露（Z-6 落地要求）。
        """
        days = [f"202401{d:02d}" for d in range(2, 12)]     # 10 个交易日
        crisis_day, watch_day = days[0], days[3]
        monkeypatch.setattr(
            "btf.data.liq.index_pct",
            lambda _sym, ymd, root=None: (
                -9.0 if ymd == crisis_day else (-4.0 if ymd == watch_day
                                                else 1.0)))
        monkeypatch.setattr(
            "btf.data.liq.limit_stats", lambda _ymd, root=None: (0, 100, 0.0))

        prev = None
        out: list[tuple[str, str, str, int]] = []
        for day in days:
            state = state_of(day, TH, prev=prev)
            out.append((state.raw, state.status, state.phase, state.phase_days))
            prev = state

        assert out[3][0] == WATCH and out[3][1] == CRISIS, out[3]
        assert out[3][2:] == (CONFIRM, 0), "WATCH 打断后确认计数须归零"
        assert [s for _r, s, _p, _d in out] == [
            CRISIS, CRISIS, CRISIS, CRISIS, CRISIS, CRISIS,
            RECOVERY, RECOVERY, RECOVERY, NORMAL], out

    def test_recovery_window_is_bounded_under_long_calm(self, monkeypatch):
        """长平静期（60 日）不产生任何 RECOVERY（窗口有界，非自维持）。"""
        days = [f"2024{m:02d}{d:02d}" for m in range(1, 4)
                for d in range(1, 21)][:60]
        monkeypatch.setattr(
            "btf.data.liq.index_pct", lambda _sym, ymd, root=None: 1.0)
        monkeypatch.setattr(
            "btf.data.liq.limit_stats", lambda _ymd, root=None: (0, 100, 0.0))
        prev = None
        for day in days:
            state = state_of(day, TH, prev=prev)
            assert state.status == NORMAL, (day, state.status)
            prev = state

    def test_cap_recovery_is_monotone(self):
        """仓位上限**单调恢复**：危机/确认期 0 → 恢复期 1 成 → NORMAL 5 成。

        Z-6 的核心裁决点（19 号 §21.3）：若确认期按 NORMAL 处理，危机解除后
        会立刻回到 5 成上限，与「逐步恢复」本义相反。
        """
        seq = [CRISIS, CRISIS, RECOVERY, NORMAL]
        caps = [POSITION_CAPS[s] for s in seq]
        assert caps == sorted(caps), caps
        assert caps == [0.0, 0.0, 10.0, 50.0]

    def test_position_caps(self):
        assert POSITION_CAPS[CRISIS] == 0.0
        assert POSITION_CAPS[NORMAL] == 50.0
        assert POSITION_CAPS[RECOVERY] == 10.0


class TestV77Strategy:
    def _strategy(self, status, picks, **kw) -> V77Strategy:
        def liq_fn(ymd, thresholds, root=None, prev=None):
            return LiqState(date=ymd, status=status, sh_pct=0.0, small_pct=0.0,
                            down_cnt=0, down_ratio=0.0, triggers=(),
                            raw=status)

        return V77Strategy(FakeRules(), top_k=3, liq_fn=liq_fn,
                           screen_fn=lambda *a, **k: picks, **kw)

    def test_crisis_goes_flat(self):
        strategy = self._strategy(CRISIS, ["000001.SZ", "600000.SH"])
        ctx = FakeCtx()
        strategy.on_close(ctx, _day("20240103"))
        assert ctx.targets[-1].targets == {}

    def test_normal_equal_weight_within_cap(self):
        """NORMAL：top_k 等权，总仓位 ≤ 5 成。"""
        strategy = self._strategy(NORMAL, ["000001.SZ", "600000.SH",
                                           "000651.SZ"])
        ctx = FakeCtx()
        strategy.on_close(ctx, _day("20240103"))
        targets = ctx.targets[-1].targets
        assert len(targets) == 3
        assert sum(targets.values()) == pytest.approx(0.5)
        assert len(set(targets.values())) == 1

    def test_watch_caps_at_two_tenths(self):
        strategy = self._strategy(WATCH, ["000001.SZ", "600000.SH"])
        ctx = FakeCtx()
        strategy.on_close(ctx, _day("20240103"))
        assert sum(ctx.targets[-1].targets.values()) == pytest.approx(0.2)

    def test_monthly_rebalance_only_once(self):
        strategy = self._strategy(NORMAL, ["000001.SZ"])
        ctx = FakeCtx()
        for ymd in ("20240102", "20240103", "20240104", "20240201"):
            strategy.on_close(ctx, _day(ymd))
        assert len(ctx.targets) == 2          # 1 月一次 + 2 月一次

    def test_empty_screen_goes_flat(self):
        strategy = self._strategy(NORMAL, [])
        ctx = FakeCtx()
        strategy.on_close(ctx, _day("20240103"))
        assert ctx.targets[-1].targets == {}

    def test_last_state_recorded(self):
        """Z-6 单步递推：策略保留**前一日状态**（确认/恢复期记忆随之回传）。"""
        strategy = self._strategy(CRISIS, ["000001.SZ"])
        ctx = FakeCtx()
        strategy.on_close(ctx, _day("20240103"))
        strategy.on_close(ctx, _day("20240104"))
        assert strategy._last_state is not None
        assert strategy._last_state.status == CRISIS
        assert strategy.last_state is not None
