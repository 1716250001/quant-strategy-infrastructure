# -*- coding: utf-8 -*-
"""JSON Schema 校验（ADR-4；06 §11.5；M1 任务 4.3）。

契约路由：schema_version（如 "backtest.v1"）→ schemas/<version>.json；
未知版本显式报错（不做隐式迁移）。

错误可读性验收（06 §11.5）：一次性全量列出（iter_errors 不短路），
每条 = JSON 路径 + 消息 + 违例值（截断 repr）；H2（rules 段字面量
拒绝）由 Schema additionalProperties:false 落地，报错原文含键名。
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from jsonschema import Draft202012Validator

_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"

_KNOWN = {"backtest.v1"}


class ConfigError(ValueError):
    """配置校验失败（全部违例聚合；str() 为多行可读报告）。"""

    def __init__(self, problems: list[str]):
        self.problems = problems
        head = f"配置校验失败（{len(problems)} 处）：" if problems else "配置校验失败"
        super().__init__("\n".join([head] + [f"  [{i}] {p}" for i, p in enumerate(problems, 1)]))


def _fmt_path(error) -> str:
    parts = [str(p) for p in error.absolute_path]
    return ".".join(parts) if parts else "<顶层>"


def _fmt_value(error) -> str:
    v = error.instance
    text = repr(v)
    return text if len(text) <= 60 else text[:57] + "..."


def validate(config: Mapping[str, object]) -> None:
    """按 schema_version 路由校验；违例聚合抛 ConfigError。"""
    version = config.get("schema_version")
    if not isinstance(version, str) or not version:
        raise ConfigError(["schema_version 缺失（四层合并后仍无值——默认层被显式置空？）"])
    schema_path = _SCHEMA_ROOT / f"{version}.json"
    if not schema_path.exists():
        raise ConfigError([
            f"schema_version: 未知契约版本 {version!r}（可用: {sorted(_KNOWN)}）"])
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    problems = [
        f"{_fmt_path(e)}: {e.message}（值 {_fmt_value(e)}）"
        for e in sorted(validator.iter_errors(config), key=lambda e: list(e.absolute_path))
    ]
    if problems:
        raise ConfigError(problems)


__all__ = ["ConfigError", "validate"]
