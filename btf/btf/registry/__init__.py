# -*- coding: utf-8 -*-
"""btf.registry：插件注册表（04 §8.5；M1 任务 4.2）。

配置声明制：用户在 YAML 写插件名与参数，registry.resolve/create 完成
名字解析与契约版本协商（未知名显式报错；主版本不符拒载——04 §8.7）。
本包属外层（cli | viz | registry，03 §7.2），可 import 全部内层。
"""
from btf.registry.catalog import (
    ANALYZER,
    COST_MODEL,
    DATA_FEED,
    EXECUTION_HANDLER,
    OPTIMIZER,
    REBALANCER,
    RISK_RULE,
    RULES_PROVIDER,
    RUN_STORE,
    SLIPPAGE_MODEL,
    RegistryError,
    available,
    create,
    points,
    register,
    resolve,
)

__all__ = [
    "ANALYZER", "COST_MODEL", "DATA_FEED", "EXECUTION_HANDLER",
    "OPTIMIZER", "REBALANCER", "RISK_RULE", "RULES_PROVIDER",
    "RUN_STORE", "SLIPPAGE_MODEL",
    "RegistryError", "available", "create", "points", "register", "resolve",
]
