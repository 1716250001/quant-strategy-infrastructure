# -*- coding: utf-8 -*-
"""`btf.runtime` —— 装配根（04 §8.3.8；R4 拆分后的**包**形态）。

拆分前的 `runtime.py`（870 行）把「配置解析 / 宇宙解析 / 组件装配 / 运行编排」
四类职责混在一个 `BTFRuntime` 类里——改任一处都要在长方法里定位（19 号 §9.3 / R4）。

现按职责下沉到协作模块，**门面只保留状态与委托**：

| 模块 | 职责 |
|---|---|
| `btf.runtime.config_resolver` | 配置四层合并 + Schema 校验（**ConfigResolver**） |
| `btf.runtime.universe_resolver` | 标的宇宙解析（explicit / all / index）（**UniverseResolver**） |
| `btf.runtime.component_assembler` | 插件装配、策略加载、规则源、装配期硬门与守卫（**ComponentAssembler**） |
| `btf.runtime.run_orchestrator` | 落盘编排（create_run → 引擎 → 产物 → 指标 → 状态补全）（**RunOrchestrator**） |
| `btf.runtime._runtime` | `BTFRuntime` 门面（状态 + 委托） |

**对外 API 不变**（`BTFRuntime` / `make_store` / `fee_segments` / `DataQualityError` /
`SHORT_PERIOD_TRADING_DAYS`），所有 `from btf.runtime import …` 的调用方无需改动。

依赖纪律：`btf.runtime` 是**装配根**，可触达 registry/data/engine/… 全部内层
（不参与 `Layered architecture` 的分层序号，故不构成逆向依赖）；
入口层（cli/api）→ runtime 的边由契约新 10 的**门面豁免**显式留痕。
"""
from btf.runtime._runtime import (
    SHORT_PERIOD_TRADING_DAYS,
    BTFRuntime,
    DataQualityError,
    fee_segments,
    make_store,
)

__all__ = [
    "SHORT_PERIOD_TRADING_DAYS",
    "BTFRuntime",
    "DataQualityError",
    "fee_segments",
    "make_store",
]
