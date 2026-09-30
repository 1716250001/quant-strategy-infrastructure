# -*- coding: utf-8 -*-
"""v7.7 规则策略：L2 硬筛选股 + L0-LIQ 状态控仓（M3 任务 6.2）。

装配（H2 单一真源）：
    规则参数**只**来自 RulesProvider（mirror_json 镜像 JSON），`rules` 由
    runtime 在装配期注入（策略 `__init__` 声明 `rules` 参数即注入）——YAML
    `run.params` 不得出现规则参数字面量（Schema 拒绝 + 四方机检）。

    策略: "btf.strategy.v77:V77Strategy"
    params: {top_k: 10}          # 仅研究变量（非规则参数）
    rules: {source: "mirror_json", path: "...", overrides: []}

逻辑（每月首个交易日调仓）：
    ① L0-LIQ 状态（data/liq.py）→ 仓位上限（NORMAL 5 成 / WATCH 2 成 /
       CRISIS 0 / RECOVERY 1 成，来自规则源 L76）；
    ② L2 硬筛（data/fundamentals.py，PIT 口径）→ 合格标的；
    ③ 低估优先（pe_ttm 升序）取前 top_k，**等权**且总仓位 ≤ 上限。

可测性：`screen_fn` / `liq_fn` 可注入（单测用合成实现，脱离主库）。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from btf.data.fundamentals import screen_l2
from btf.data.liq import POSITION_CAPS, LiqState, state_of
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio


class V77Strategy(StrategyBase):
    """赤潮 v7.7 规则策略（L2 硬筛 + L0-LIQ 控仓）。"""

    contract_version = CONTRACT_VERSION

    def __init__(
        self,
        rules: object,
        *,
        top_k: int = 10,
        root: str | None = None,
        rebalance: str = "monthly",
        caps: Mapping[str, float] | None = None,
        screen_fn: Callable[..., Sequence[str]] | None = None,
        liq_fn: Callable[..., LiqState] | None = None,
        liq_series: Mapping[str, LiqState] | None = None,
    ):
        """
        rules        RulesProvider（镜像 JSON；装配期注入）
        top_k        持有数量（研究变量）
        root         主库根（None = 默认 MARKET_DATA_DIR；screen_fn 注入时忽略）
        rebalance    "monthly"（首个交易日）| "daily"
        caps         状态→仓位上限 %（None = 规则源口径 POSITION_CAPS）
        screen_fn    注入的选股函数 (date_ymd, params, root) → 代码列表（测试用）
        liq_fn       注入的状态函数 (date_ymd, thresholds, root, prev) → LiqState
        liq_series   **区间预计算的 LIQ 序列**（19 号报告 P0-1/§9.2；runtime
                     装配期注入）——命中即查表（O(1)），未命中回退单日口径
                     （诚实降级：区间外日期仍走 `state_of`）
        """
        self.rules = rules
        self.top_k = int(top_k)
        self.root = root
        self.rebalance = rebalance
        self.caps = dict(caps) if caps else dict(POSITION_CAPS)
        self._screen = screen_fn or screen_l2
        self._liq = liq_fn or state_of
        self._liq_series = dict(liq_series) if liq_series else None
        self._last_key: str | None = None
        self._last_state: LiqState | None = None   # 前一日状态（单步递推；Z-6）
        self.last_state: LiqState | None = None

    # ── 策略钩子 ──
    def _state_on(self, ymd: str) -> LiqState:
        """当日 LIQ 状态：预计算序列优先（十年 3.9s vs 逐日 760s），
        未覆盖日期回退 `state_of`（口径同一实现 `liq._finalize`）。"""
        if self._liq_series is not None:
            cached = self._liq_series.get(ymd)
            if cached is not None:
                return cached
        state = self._liq(ymd, self.rules.get("L0_LIQ"), root=self.root,
                          prev=self._last_state)
        # Z-6：确认期 + 恢复期为**单步递推**（机器记忆 = phase/phase_days）
        self._last_state = state
        return state

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        ymd = date.to_ymd()
        key = ymd[:6] if self.rebalance == "monthly" else ymd
        state = self._state_on(ymd)
        self.last_state = state
        if key == self._last_key:
            return
        self._last_key = key

        cap = float(self.caps.get(state.status, 0.0))
        if cap <= 0 or not self.top_k:
            ctx.submit_target(TargetPortfolio(date, {}))
            return
        picks = list(self._screen(ymd, self.rules.get("L2_filter"),
                                  root=self.root))[:self.top_k]
        if not picks:
            ctx.submit_target(TargetPortfolio(date, {}))
            return
        weight = min(cap / 100.0 / len(picks), 1.0)
        ctx.submit_target(TargetPortfolio(date, {s: weight for s in picks}))


__all__ = ["V77Strategy"]
