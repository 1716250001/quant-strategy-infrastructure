# -*- coding: utf-8 -*-
"""RulesProvider 协议（04 §8.3.7 规则参数单一真源；M1 任务 4.1 契约定稿）。

铁律（H2）：赤潮体系规则唯一源 = 赤潮/rules/single-source.md（v7.7）；
回测侧参数必须经 RulesProvider 加载，YAML 出现规则参数字面量（未声明
override: true）→ Schema 校验拒绝（由 btf/schemas/backtest.v1.json
rules 段落地，M1 任务 4.3）。

M1 交付：协议 + StaticRulesProvider（契约冒烟/单测/非规则源策略的最小
实现）。

M3 任务 6.1 交付：**MirrorJsonRulesProvider**——读 `rules_mirror_v77.json`
（由 `tools/export_rules_mirror.py` 从 md_core 镜像区导出），使回测侧
规则参数**单一真源**（H2：YAML 不得出现规则参数字面量）。

规则镜像 JSON 结构（`rules_mirror.v1`）：
    {
      "schema_version": "rules_mirror.v1",
      "rules_version": "v7.7@sha256:<规则内容哈希>",   # 进 manifest
      "source": {"file": ..., "version": "v7.7"},
      "units": {"roe_min": "percent", ...},            # 单位显式（口径坑）
      "rules": {"L2_filter": {...}, "L0_LIQ": {...}},
      "aliases": {"pe_ttm_max": "pe_max", ...}         # 文档键名 → 镜像键名
    }

单位口径纪律：镜像 JSON **以 md_core 镜像区为准**（roe_min/dv_min 为
**百分数**，如 5.0 表示 5%），并在 `units` 中显式声明——04 §8.3.7 文档侧
的 `roe_waa_min=0.05`（小数）经 `aliases` 映射到同一参数，避免双口径。
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, Protocol

from btf.domain.contracts import CONTRACT_VERSION


class RulesProvider(Protocol):
    """规则参数提供者：从规则源（或其镜像导出 JSON）加载，而非 YAML 手抄。

    rules_version() 的指纹（如 "v7.7+<内容哈希>"）进 manifest
    （rules_version 字段，08 §13.2 四版本+规则版本）。
    """

    def rules_version(self) -> str: ...

    def get(self, rule_id: str) -> Mapping[str, Any]: ...


class StaticRulesProvider:
    """静态注入实现（构造方持有参数表；测试与非规则源策略用）。

    非规则源场景的定位：通用策略（非赤潮规则引擎）不受 H2 约束，
    参数可直接写 YAML params 段；本实现用于单测与 registry 冒烟。
    """

    contract_version: ClassVar[str] = CONTRACT_VERSION

    def __init__(self, params: Mapping[str, Mapping[str, Any]],
                 version: str = "static"):
        self._params = {k: dict(v) for k, v in params.items()}
        self._version = version

    def rules_version(self) -> str:
        return self._version

    def get(self, rule_id: str) -> Mapping[str, Any]:
        if rule_id not in self._params:
            raise KeyError(
                f"未登记规则 {rule_id!r}（已登记: {sorted(self._params)}）")
        return dict(self._params[rule_id])


class MirrorJsonRulesProvider:
    """规则镜像 JSON 提供者（M3 任务 6.1；04 §8.3.7 单一真源）。

    H2 落地：回测侧规则参数**只从镜像 JSON 读**，YAML 出现规则参数字面量
    由 Schema 拒绝；显式 override 须声明 `override: true` 并留痕进 manifest
    （`rules_overrides` 字段）。

    **R66′/P-NEW-1（2026-10-12）override 生效语义**：此前 overrides **只留痕
    不应用**（`get()` 直读镜像值）⇒ 规则参数网格轴不可用。现定案：
    - **构造期一次性浅合并**（immutable 快照）：条目级覆盖同键参数、追加新键，
      **不做深层 merge**；`get()` 返回合并后结果；
    - **键名归一**：条目的规则 ID 与参数键均与 `get()` 同走 `aliases`
      （文档键名 → 规范名，如 `pe_ttm_max → pe_max`）；
    - **fail-closed**：条目指向未登记规则 ⇒ 构造期 `ValueError`（早失败，
      禁静默 no-op——正是本次缺陷的形态）；空 `overrides` ⇒ 行为逐位不变；
    - **留痕集＝生效集**：`overrides` 属性返回**规范化后的生效条目**
      （`{rule_id, param, value}`，均为规范名）——与 manifest 里的 config
      原文条目可断言等价（归一后一致，G4）。
    """

    contract_version: ClassVar[str] = CONTRACT_VERSION

    def __init__(self, path: str | Path,
                 overrides: Sequence[Mapping[str, Any]] = ()):
        self.path = Path(path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.schema_version: str = payload.get("schema_version", "")
        self._rules: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in (payload.get("rules") or {}).items()}
        self._aliases: dict[str, str] = dict(payload.get("aliases") or {})
        self._units: dict[str, str] = dict(payload.get("units") or {})
        self._version: str = payload.get("rules_version", "unknown")
        # ── R66′/P-NEW-1：构造期一次性合并（生效语义）＋ 规范化留痕 ──
        self._overrides: tuple[Mapping[str, Any], ...] = tuple(
            self._normalize_override(raw) for raw in overrides)
        for entry in self._overrides:
            key, param, value = (entry["rule_id"], entry["param"],
                                 entry["value"])
            if key not in self._rules:
                raise ValueError(
                    f"override 指向未登记规则 {key!r}"
                    f"（已登记: {sorted(self._rules)}）——fail-closed，"
                    "禁静默 no-op（R66′/P-NEW-1 教训）")
            self._rules[key][param] = value      # 浅合并：覆盖/追加

    def _normalize_override(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """条目规范化：`rule_id|rule` 二选一 ＋ `param`/`value` 必填；键名走 aliases。"""
        if not isinstance(raw, Mapping):
            raise ValueError(
                f"override 条目须为映射，得 {type(raw).__name__}: {raw!r}")
        rule_id = raw.get("rule_id", raw.get("rule"))
        param = raw.get("param")
        if not rule_id or not param or "value" not in raw:
            raise ValueError(
                f"override 条目缺 rule_id(/rule)、param 或 value: {dict(raw)}")
        return {"rule_id": self._aliases.get(rule_id, rule_id),
                "param": self._aliases.get(param, param),
                "value": raw["value"]}

    # ── 协议 ──
    def rules_version(self) -> str:
        """规则源版本指纹（如 "v7.7@sha256:ab12…"）。"""
        return self._version

    def get(self, rule_id: str) -> Mapping[str, Any]:
        """取规则参数（支持 aliases 文档键名；未登记 → KeyError 显式报错）。

        R66′ 起：返回**合并 overrides 后**的快照（构造期已应用）。
        """
        key = self._aliases.get(rule_id, rule_id)
        if key not in self._rules:
            raise KeyError(
                f"未登记规则 {rule_id!r}（已登记: {sorted(self._rules)}）")
        return dict(self._rules[key])

    # ── 便捷（报告/机检用）──
    @property
    def rule_ids(self) -> list[str]:
        return sorted(self._rules)

    @property
    def overrides(self) -> tuple[Mapping[str, Any], ...]:
        """显式 override 清单——R66′ 起＝**规范化生效条目**（留痕集＝生效集）。"""
        return self._overrides

    def units(self) -> Mapping[str, str]:
        """参数单位表（percent/ratio——口径显式化，防双口径）。"""
        return dict(self._units)


__all__ = ["MirrorJsonRulesProvider", "RulesProvider", "StaticRulesProvider"]
