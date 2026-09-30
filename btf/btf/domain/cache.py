# -*- coding: utf-8 -*-
"""有界缓存容器（PF-9 / P2-5，19 号附录 D.3）：稳定核纯 stdlib 工具。

为什么放在 `btf.domain`：本模块**零第三方依赖**（铁律 3 白名单内），可被
任意层 import（`config | domain` 是最内层），从而替代散落各层的"无界 dict"。

背景（P2-5 审计）：全库 5 处无界缓存——`domain/types._YMD_CACHE`、
`data/feed._bars_cache`、`data/adjust._cache`、`engine/loop.history_cache`、
`strategy/context._cache`——长跑内存不可控（键随输入规模增长）。
今统一为 `BoundedDict`（LRU + 容量上限 + 可观测统计）。

纪律：
    - 容量必须**显式**声明（各站点按自身访问模式取值，不留默认黑洞）；
    - 语义与原 dict 一致（`get`/`[]`/`in`/`len`/`clear`/`pop`），仅多"淘汰"；
    - `stats()` 供性能断言与披露使用（不得恒空——铁律新 16）。
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, MutableMapping
from typing import Any

__all__ = ["BoundedDict"]


class BoundedDict[K, V](MutableMapping[K, V]):
    """LRU 有界映射（读命中即置新；写满淘汰最久未用）。

    >>> c = BoundedDict[int, int](maxsize=2)
    >>> c[1] = 1; c[2] = 2; _ = c[1]; c[3] = 3
    >>> sorted(c)            # 1 刚被读 → 淘汰 2
    [1, 3]
    """

    def __init__(self, maxsize: int, *, name: str = "") -> None:
        if maxsize <= 0:
            raise ValueError(f"maxsize 须 > 0，得 {maxsize}")
        self.maxsize = int(maxsize)
        self.name = name or "bounded"
        self._data: OrderedDict[K, V] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    # ── 读 ──
    def __getitem__(self, key: K) -> V:
        try:
            value = self._data[key]
        except KeyError:
            self.misses += 1
            raise
        self.hits += 1
        self._data.move_to_end(key)
        return value

    def get(self, key: K, default: Any = None) -> Any:     # type: ignore[override]
        if key in self._data:
            return self[key]
        self.misses += 1
        return default

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    # ── 写 ──
    def __setitem__(self, key: K, value: V) -> None:
        if key in self._data:
            self._data.move_to_end(key)
            self._data[key] = value
            return
        self._data[key] = value
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)
            self.evictions += 1

    def __delitem__(self, key: K) -> None:
        del self._data[key]

    def clear(self) -> None:
        self._data.clear()

    # ── 观测（铁律新 16：不得恒空）──
    def stats(self) -> dict[str, Any]:
        """缓存统计（容量/条目/命中/未命中/淘汰）。"""
        return {"name": self.name, "maxsize": self.maxsize,
                "entries": len(self._data), "hits": self.hits,
                "misses": self.misses, "evictions": self.evictions}

    def __repr__(self) -> str:                              # pragma: no cover
        return (f"BoundedDict({self.name!r}, maxsize={self.maxsize}, "
                f"entries={len(self._data)}, evictions={self.evictions})")
