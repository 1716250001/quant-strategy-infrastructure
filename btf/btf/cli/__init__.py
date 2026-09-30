# -*- coding: utf-8 -*-
"""L5 CLI 层：bt 命令族（注册表式子命令）。

**十一命令**（与 `main.py::COMMANDS` 一致）：`config-check` / `run` / `report` / `verify` / `test` /
`dataset` / `optimize` / `check` / `cold-backup` / `runs` / `show`。

纪律：入口层**只编排不承载业务**（契约新 10）——编排统一在 `btf.app`（环境过滤/覆盖解析/run 主链路/
报告装配/dataset 编排/门禁与冷备委托/只读产物查询），CLI 只做参数解析与转发。

门禁：`bt check` **已收编**（CLI 审查报告-20260930 P0）为九项架构门禁的子命令，委托
`tools/check.py` 单一真源（`bt check` ≡ `python tools/check.py`，同退出码）；冷备同理
（`bt cold-backup` → `tools/cold_backup.py`）。此前文档长期写的 `bt check` 不是命令——该
"文档与 CLI 脱节"已消除，文档/CHANGELOG/记忆自此与本表同口径。
"""
