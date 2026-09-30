# -*- coding: utf-8 -*-
"""版本单一真源（v0.5）：`__init__`、`pyproject.toml`（dynamic）与 manifest
共用——子模块取版本**不经包根**（`from btf import __version__` 会形成
experiment→btf→api 假链，import-linter 拦截；详见 pyproject 契约注记）。"""
__version__ = "1.0.0"
