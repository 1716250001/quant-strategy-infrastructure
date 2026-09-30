# -*- coding: utf-8 -*-
"""示例策略：月度动量轮动（18 号 5.10 DXP 验收示例；04 §8.3.2 策略协议）。

策略逻辑（教学/冒烟用途，非投资建议）：
    每月首个交易日，按**回看窗口动量**（收盘价涨跌幅）对宇宙排序，取前
    ``top_k`` 只**等权**持有——目标权重经 ``ctx.submit_target`` 声明，执行
    由 Rebalancer/ExecutionHandler 完成（信号-执行分离，04 §8.2.7）。

用法：
    bt run --config examples/config_rotation.yaml
    bt report --run <run_id>

约束（04 §8.3.2 ctx API 边界）：只读 ctx 暴露的行情/持仓/宇宙，**无 I/O**
（不 open 文件、不访问网络）——策略层可测性的前提。
"""
from __future__ import annotations

from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import TradingDate
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio


class MonthlyMomentumRotation(StrategyBase):
    """月度动量轮动（top_k 等权）。

    DXP 验收路径（`config_rotation.yaml`）——此前因配置死键被 config-check
    拦下，修复后（X-5）才暴露本处缺失：策略未声明 `contract_version` 会被
    runtime 版本协商拒绝（04 §8.7 S1；v0.5.1 P1-2/EX-2 起强制）。
    """

    #: S1 契约声明（runtime 装配期版本协商必需）
    contract_version = CONTRACT_VERSION

    def __init__(self, top_k: int = 10, lookback: int = 20,
                 weight: float | None = None):
        """
        top_k     持有数量（动量排名前 K）
        lookback  动量回看窗口（交易日）
        weight    单标的目标权重（None = 等权 1/K）
        """
        self.top_k = int(top_k)
        self.lookback = int(lookback)
        self.weight = weight
        self._last_month: str | None = None

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        month = date.to_ymd()[:6]
        if month == self._last_month:          # 每月首个交易日调仓
            return
        self._last_month = month

        scores: dict[str, float] = {}
        for inst in ctx.universe():
            bars = ctx.history(inst.symbol, end=date, n_bars=self.lookback + 1)
            if len(bars) < 2:
                continue
            first, last = bars[0].close, bars[-1].close
            if first > 0:
                scores[inst.symbol] = last / first - 1.0
        if not scores:
            return
        ranked = sorted(scores, key=lambda s: -scores[s])[:self.top_k]
        weight = self.weight if self.weight is not None else 1.0 / len(ranked)
        ctx.submit_target(TargetPortfolio(date, {s: weight for s in ranked}))


__all__ = ["MonthlyMomentumRotation"]
