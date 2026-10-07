# -*- coding: utf-8 -*-
"""版本单一真源（v0.5）：`__init__`、`pyproject.toml`（dynamic）与 manifest
共用——子模块取版本**不经包根**（`from btf import __version__` 会形成
experiment→btf→api 假链，import-linter 拦截；详见 pyproject 契约注记）。

**1.1.0 → 1.2.0**（2026-10-07；19 号 §79.7 第 4 项）：前三层完备度收口版本——
RC-01..RC-16（2026-10-05 交付、未 bump）＋ 2026-10-06 治理/订正批次（GG/§66/F8/F9/
H/N4 加固）＋ 前三层加固 CL-1..CL-4（2026-10-06）＋ 收尾批 N11..N15（2026-10-06）
＋ **CL-2 第二段**（配股/缩股入链路，Q-4(a) 默认全额行权；2026-10-07）合并落此版本。
"""
__version__ = "1.2.0"
