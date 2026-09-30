# -*- coding: utf-8 -*-
"""敏感项脱敏（ADR-4；M1 任务 4.3）。

键名含敏感词（不区分大小写）→ 值替换 "***"；manifest 落盘与日志
输出统一走本模块（04 §8.5：敏感项脱敏后入 manifest）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

_SENSITIVE = ("token", "secret", "password", "api_key", "apikey",
              "access_key", "credential")
_MASK = "***"


def _is_sensitive(key: str) -> bool:
    low = key.lower()
    return any(s in low for s in _SENSITIVE)


def mask_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """递归脱敏副本（原配置不可变；嵌套映射/序列均覆盖）。"""
    out: dict[str, Any] = {}
    for k, v in config.items():
        if isinstance(v, Mapping) and not _is_sensitive(k):
            out[k] = mask_config(v)
        elif isinstance(v, Sequence) and not isinstance(v, (str, bytes)) \
                and not _is_sensitive(k):
            out[k] = [
                mask_config(item) if isinstance(item, Mapping) else deepcopy(item)
                for item in v
            ]
        else:
            out[k] = _MASK if _is_sensitive(k) else deepcopy(v)
    return out


__all__ = ["mask_config"]
