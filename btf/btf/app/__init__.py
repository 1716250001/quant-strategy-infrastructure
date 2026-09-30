# -*- coding: utf-8 -*-
"""`btf.app` —— 应用服务层（P2-3 / R4；19 号 §9.3 应用编排层）。

CLI 与 API 的**共享编排**集中于此；两个入口层只做参数解析与转发
（铁律新 10 的落地形态）。逐条对应与未收敛项见 `btf.app.services` 模块 docstring。
"""
from btf.app.services import (
    RunOutcome,
    assemble_assumptions,
    build_dataset,
    build_report_file,
    filter_btf_env,
    parse_override_value,
    parse_overrides,
    run_backtest,
)

__all__ = [
    "RunOutcome",
    "assemble_assumptions",
    "build_dataset",
    "build_report_file",
    "filter_btf_env",
    "parse_override_value",
    "parse_overrides",
    "run_backtest",
]
