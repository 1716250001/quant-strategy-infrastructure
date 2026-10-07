# -*- coding: utf-8 -*-
"""危机区间清单（**外置常量 + 可配置覆盖**；RC-13，2026-10-05）。

来源（外部基准对照审查 P1-5）："危机区间未隔离（2015 股灾 / 2018 熊 /
2020 疫情 / 2024 初流动性冲击全被并进 P1–P3）" ⇒ 要求清单**外置**，
使"本结论跨过哪些危机"在产物里**可见**，且口径单点可改、可被配置覆盖。

粒度：**月级闭区间**（口径保守——宁可多含，不可漏含；精确起止日属研究
议题，可经 `analysis.crisis_windows` 覆盖）。

消费：runtime 装配期披露（`assembly_notes`）——回测区间与危机窗重叠即登记
（[04 §8.6] 降级/披露语义的同族："结论前提必须可见"）。**只披露、不改数**；
**不阻断**（危机区间内的回测本身是合法研究动作——不可对比的是"跨期"表述）。

纪律（"**基准必须在跑前定死**"）已于 2026-10-06（配置类同步批）归位
`20-回测平台使用指引.md` §0 **纪律三**（基准跑前定死 / 危机区间不得事后剔除；
另见该文 §7 `analysis.crisis_windows` 配置说明）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["CRISIS_WINDOWS", "CrisisWindow", "from_config", "intersect"]


@dataclass(frozen=True)
class CrisisWindow:
    """危机窗口（闭区间；`start`/`end` 为 `YYYY-MM-DD` 字符串）。"""

    name: str
    start: str
    end: str

    def overlaps(self, start_ymd: str, end_ymd: str) -> bool:
        """与 [start_ymd, end_ymd] 是否重叠（闭区间）。"""
        return start_ymd <= self.end and self.start <= end_ymd


#: 内置危机窗（月级；P1-5 原文清单：2015-06~2016-01 / 2018-01~2018-12 /
#: 2020-01~2020-03 / 2022-01~2022-10 / 2024-01~2024-02）
CRISIS_WINDOWS: tuple[CrisisWindow, ...] = (
    CrisisWindow("2015 股灾（含 2016-01 熔断）", "2015-06-01", "2016-01-31"),
    CrisisWindow("2018 熊市", "2018-01-01", "2018-12-31"),
    CrisisWindow("2020 疫情冲击", "2020-01-01", "2020-03-31"),
    CrisisWindow("2022 调整", "2022-01-01", "2022-10-31"),
    CrisisWindow("2024 初流动性冲击", "2024-01-01", "2024-02-29"),
)


def _is_ymd(text: str) -> bool:
    return (len(text) == 10 and text[4] == "-" and text[7] == "-"
            and text.replace("-", "").isdigit())


def from_config(
    cfg: Mapping[str, Any] | None,
) -> tuple[CrisisWindow, ...]:
    """`analysis.crisis_windows` 覆盖（缺省/空 ⇒ 内置常量表）。

    配置形如::

        analysis:
          crisis_windows:
            - {name: "2015 股灾", start: "2015-06-01", end: "2016-01-31"}

    校验（fail-closed——口径数据不得静默错读）：`name` 非空、`start`/`end`
    形如 `YYYY-MM-DD` 且 `start ≤ end`，否则 `ValueError`。
    """
    rows: Any = None
    if cfg:
        rows = (cfg.get("analysis") or {}).get("crisis_windows")
    if not rows:
        return CRISIS_WINDOWS
    out: list[CrisisWindow] = []
    for i, row in enumerate(rows):
        name = str((row or {}).get("name") or "")
        start = str((row or {}).get("start") or "")
        end = str((row or {}).get("end") or "")
        if not name or not _is_ymd(start) or not _is_ymd(end) or start > end:
            raise ValueError(
                f"analysis.crisis_windows[{i}] 非法：{dict(row or {})!r}"
                f"（须 name 非空 + start/end=YYYY-MM-DD 且 start ≤ end）")
        out.append(CrisisWindow(name, start, end))
    return tuple(out)


def intersect(
    start_ymd: str, end_ymd: str,
    windows: Sequence[CrisisWindow] | None = None,
) -> tuple[CrisisWindow, ...]:
    """与 [start_ymd, end_ymd] 重叠的危机窗（缺省用内置常量表）。"""
    return tuple(w for w in (windows if windows is not None else CRISIS_WINDOWS)
                 if w.overlaps(start_ymd, end_ymd))
