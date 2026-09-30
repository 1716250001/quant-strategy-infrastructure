# -*- coding: utf-8 -*-
"""L5 CLI 层：bt 命令族（注册表式子命令）。

**七命令**（与 `main.py::COMMANDS` 一致）：`config-check` / `run` / `report` / `verify` / `test` /
`dataset` / `optimize`。

纪律：入口层**只编排不承载业务**（契约新 10）——编排统一在 `btf.app`（环境过滤/覆盖解析/run 主链路/
报告装配/dataset 编排），CLI 只做参数解析与转发。

注：架构门禁 `python tools/check.py`（八项）是**独立脚本**，不是 `bt` 子命令。
"""
