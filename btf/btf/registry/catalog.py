# -*- coding: utf-8 -*-
"""插件注册表：名字表 + 配置声明制发现 + 版本协商（04 §8.5；M1 任务 4.2）。

MVP 纪律（04 §8.5「不做 entry-points 自动发现」）：
    - 内置插件 = 本模块 _BUILTIN 名字表（唯一登记点）；
    - 扩展插件 = 启动期程序化 ``register()``（如运行器注入 MemoryFeed 场景）；
    - 用户侧只写**名字 + 参数**（YAML 配置声明制），不写代码路径。

版本协商（04 §8.7，ADR 铁律）：``resolve/create`` 先取插件
``contract_version`` 与宿主协商——未声明 / 主版本不符 → 拒载
（ContractVersionError），不静默降级。

未实现扩展点（v1.0+）：无——OPTIMIZER 已于 v0.5 V5-3 交付 grid_search。

已交付（M2）：
    任务 5.4  analyzer 15 项核心指标（名字表 = metrics.METRICS）；
    任务 5.5  run_store local_jsonl（experiment/store.py：run 目录产物 +
             原子写 + manifest 四版本，载体留痕见模块 docstring）。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from btf.domain.contracts import negotiate

# ── 扩展点常量（04 §8.5 扩展点清单）──
DATA_FEED = "data_feed"
COST_MODEL = "cost_model"
SLIPPAGE_MODEL = "slippage_model"
EXECUTION_HANDLER = "execution_handler"
RISK_RULE = "risk_rule"
REBALANCER = "rebalancer"
ANALYZER = "analyzer"
RULES_PROVIDER = "rules_provider"
OPTIMIZER = "optimizer"
RUN_STORE = "run_store"

#: 插件工厂：类（config 关键字实例化）或 Callable[[Mapping], Any]
PluginFactory = Callable[..., Any]


class RegistryError(KeyError):
    """注册表违例：未知名 / 未知扩展点 / 名字重复登记。"""


#: 全部扩展点名（静态声明——`points()` 不再靠"装载一次"获得清单）
_POINTS: tuple[str, ...] = (DATA_FEED, COST_MODEL, SLIPPAGE_MODEL,
                            EXECUTION_HANDLER, RISK_RULE, REBALANCER, ANALYZER,
                            RULES_PROVIDER, OPTIMIZER, RUN_STORE)


def _load_point(point: str) -> dict[str, PluginFactory] | None:
    """**按扩展点分组惰性装载**（PF-10 / P3-3，19 号附录 D.3）。

    原实现 `_table()` 一次 import 全部 10 个扩展点的插件模块——"首次 registry
    调用即拉起全插件面"（启动/测试成本；且 `data_feed` 一点就会把
    `optimize.grid`→`runtime` 整链拉进来）。今每点独立工厂：只 import 该点
    用到的模块；返回 None 表示未知扩展点（由调用方报错）。
    """
    if point == DATA_FEED:
        from btf.data.feed import MixedDailyFeed, TushareParquetFeed
        from btf.data.memory import MemoryFeed

        return {"tushare_parquet": TushareParquetFeed,
                "mixed": MixedDailyFeed,     # V3-1（M3 6.3）：股票+ETF+指数
                "memory": MemoryFeed}
    if point == COST_MODEL:
        from btf.execution.cost import (
            AShareTieredFeeModel,
            FlatRateCostModel,
            ZeroCostModel,
        )

        return {"tiered_v1": AShareTieredFeeModel,
                "zero": ZeroCostModel,
                "flat_rate": FlatRateCostModel}
    if point == SLIPPAGE_MODEL:
        from btf.execution.cost import FixedSlippage, NoSlippage, PctSlippage

        return {"none": NoSlippage, "fixed": FixedSlippage, "pct": PctSlippage}
    if point == EXECUTION_HANDLER:
        from btf.execution.handler import NextOpenHandler

        return {"next_open": NextOpenHandler}
    if point == RISK_RULE:
        from btf.risk.rules import CashCheckRule, MaxWeightRule, TradabilityRule

        return {"max_weight": MaxWeightRule, "cash_check": CashCheckRule,
                "tradability": TradabilityRule}
    if point == REBALANCER:
        from btf.portfolio.rebalancer import FullRebalancer, ThresholdRebalancer

        return {"full": FullRebalancer,
                "threshold": ThresholdRebalancer}   # v0.5 V5-5
    if point == ANALYZER:
        from btf.analytics.metrics import METRICS

        return dict(METRICS)      # M2 任务 5.4：15 项核心指标
    if point == RULES_PROVIDER:
        from btf.strategy.rules import (
            MirrorJsonRulesProvider,
            StaticRulesProvider,
        )

        return {"static": StaticRulesProvider,       # 非规则源策略
                "mirror_json": MirrorJsonRulesProvider}  # M3 6.1
    if point == OPTIMIZER:
        from btf.optimize.grid import GridSearchOptimizer

        return {"grid_search": GridSearchOptimizer}   # v0.5 V5-3
    if point == RUN_STORE:
        from btf.experiment.store import LocalRunStore

        return {"local_jsonl": LocalRunStore}   # M2 任务 5.5
    return None


_BUILTIN: dict[str, dict[str, PluginFactory]] = {}
_USER: dict[str, dict[str, PluginFactory]] = {}


def _point_table(point: str) -> dict[str, PluginFactory]:
    if point not in _BUILTIN:
        loaded = _load_point(point)
        if loaded is None:
            raise RegistryError(
                f"未知扩展点 {point!r}（合法扩展点: {sorted(_POINTS)}）")
        _BUILTIN[point] = loaded
    return _BUILTIN[point]


def register(point: str, name: str, factory: PluginFactory) -> None:
    """程序化登记扩展插件（用户扩展/测试注入入口；04 §8.5）。

    重名 → RegistryError（不静默覆盖）；工厂须声明 contract_version。
    """
    table = _point_table(point)
    if name in table or name in _USER.get(point, {}):
        raise RegistryError(f"扩展点 {point!r} 已登记名字 {name!r}（拒绝覆盖）")
    negotiate(f"{point}:{name}", getattr(factory, "contract_version", None))
    _USER.setdefault(point, {})[name] = factory


def resolve(point: str, name: str) -> PluginFactory:
    """名字 → 工厂（先版本协商；未知名字显式报错并列出可选项）。"""
    _point_table(point)
    factory = _USER.get(point, {}).get(name) or _BUILTIN[point].get(name)
    if factory is None:
        known = sorted({*_BUILTIN.get(point, {}), *_USER.get(point, {})})
        raise RegistryError(
            f"{point} 未知插件名 {name!r}（可选: {known or '（无——扩展点待实现）'}）")
    negotiate(f"{point}:{name}", getattr(factory, "contract_version", None))
    return factory


def create(point: str, name: str, config: Mapping[str, Any] | None = None) -> Any:
    """名字 + 参数 → 实例（config 键作为构造关键字；04 §8.5 配置声明制）。

    复杂装配依赖（如 handler 需要 cost/slippage/instruments）由调用方
    （runtime/Engine）组装后构造——registry 只负责可配置插件。
    """
    factory = resolve(point, name)
    return factory(**(config or {}))


def available(point: str) -> list[str]:
    """扩展点当前可选插件名（内置 + 程序化登记）。"""
    _point_table(point)
    return sorted({*_BUILTIN.get(point, {}), *_USER.get(point, {})})


def points() -> list[str]:
    """全部扩展点名（PF-10：**静态清单**，不再触发插件装载）。"""
    return sorted(_POINTS)


__all__ = [
    "ANALYZER",
    "COST_MODEL",
    "DATA_FEED",
    "EXECUTION_HANDLER",
    "OPTIMIZER",
    "REBALANCER",
    "RISK_RULE",
    "RULES_PROVIDER",
    "RUN_STORE",
    "SLIPPAGE_MODEL",
    "RegistryError",
    "available",
    "create",
    "points",
    "register",
    "resolve",
]
