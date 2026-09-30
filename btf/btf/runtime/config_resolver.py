# -*- coding: utf-8 -*-
"""**ConfigResolver** —— 配置四层合并 + Schema 校验（R4 拆分；原 `BTFRuntime.load_config`）。

四层（后者覆盖前者）：内置默认 → `BTF_*` 环境 → 用户文件/字典 → 运行期覆盖。
拆分目的：校验与合并是**纯函数式**的一步，独立后可在不涉及宇宙/组件/引擎的
前提下被复用与测试（如批量配置预检、网格参数校验）。
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from btf.config.loader import load_config
from btf.config.validation import validate

__all__ = ["ConfigResolver"]


class ConfigResolver:
    """配置解析器：合并 + Schema 校验（失败即抛，不进入装配）。"""

    def resolve(
        self,
        source: str | Path | Mapping[str, Any] | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """返回**已校验**的合并配置（Schema 失败抛 `ConfigError`）。"""
        cfg = load_config(source, environ=environ, overrides=overrides)
        validate(cfg)
        return cfg
