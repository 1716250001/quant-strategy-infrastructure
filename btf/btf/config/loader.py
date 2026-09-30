# -*- coding: utf-8 -*-
"""L1-L4 四层配置合并（ADR-4；06 §11.5；M1 任务 4.3）。

    L1 内置默认（本模块 DEFAULTS）
    L2 环境变量 BTF_<SECTION>__<KEY>（"__" 表层间嵌套；值类型自动推断
       bool/int/float/str；BTF_DATA_DIR/BTF_OUTPUT_DIR 属路径层专用，此处跳过）
    L3 用户 YAML（pyyaml safe_load；顶层必须映射）
    L4 CLI 参数覆盖（调用方以 dict 传入；bt run --set 已交付）

合并语义：深合并（嵌套 dict 递归；非 dict 后值整替）；校验独立于合并
（btf.config.validation——先合并后校验，错误一次性全量列出）。
"""
from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")

#: 路径层专用环境变量（btf.config.paths 单一真源），不进配置合并
_PATH_ENV = {"BTF_DATA_DIR", "BTF_OUTPUT_DIR"}

#: 内置默认（04 §8.5 降级策略：handler/cost/slippage/rebalancer 有默认值；
#: 完整性策略缺省 fail-fast 不静默跳过；策略/期间/数据源必须用户显式声明）
DEFAULTS: dict[str, Any] = {
    "schema_version": "backtest.v1",
    "execution": {
        "handler": "next_open",
        "cost_model": "tiered_v1",  # 分段费率表（M2 5.1；04 §8.5 未配置→默认分段+警告）
        "slippage": {"model": "none"},
        "rebalancer": "full",
    },
    # 风控链（19 号审查 P0-3；**Q1 裁决 A —— X-7，2026-09-28**）：
    # 默认层**不声明** `allow_empty_chain` → runtime 取默认值 False →
    # 空风控链即装配期 ConfigError（fail-closed 为真默认，非"需显式打开"）。
    # 逃生开关仍在：`risk.allow_empty_chain=true` 显式声明调试意图 → 放行
    # + manifest 配置回显 + 报告假设章节强制披露（不再静默裸奔）。
    #
    # 变更留痕：v0.5.1 曾按"保守 B 方案"把默认写成 True（默认放行），与
    # CHANGELOG「fail-closed」措辞矛盾（19 号 §17.4.2）；本次按老大裁决
    # 改 A，措辞与行为自此一致。
    "risk": {"rules": []},
    # `data.completeness.missing_bars_action` 已于 v0.5.1 删除（19 号审查
    # P1-5：声明"缺 bar 即失败"但全库零消费代码——删除而非欺骗）；
    # 完整性校验由 R2.5 区间守卫（data/core/coverage）承接
    # 报告章节（P3-5 接线键）。**默认 = 全章**：原默认表缺 `reproduce` /
    # `appendix` → 真实 CLI 产物静默丢掉「复现说明」（承载 metrics_digest /
    # 数据指纹 / 代码版本 / 种子）——与 assumptions 同属"不可被关"的强制章节
    # （`viz.report._MANDATORY_SECTIONS`，BB-1 验收要求指纹在报告可见）。
    "report": {
        "sections": ["summary", "equity", "drawdown", "monthly_heat",
                     "trades", "assumptions", "reproduce", "appendix"],
    },
    # 数据不变量校验（**批 8：CC-1..CC-6**，19 号 §39.2 裁决六-1）：
    #   enabled=true  → 装配期对【运行宇宙 × 区间】校验（区间化 + 标的裁剪）
    #   strict=false  → 发现异常**告警 + 记入产物 + 报告披露**（默认；个别脏点
    #                   不阻断整轮）；true → DataQualityError（fail-closed）
    #   max_symbols=200 → 宇宙超限时按字典序前缀公平抽样（披露 sampled_symbols）
    # 三项默认均"不静默"：关闭也会在产物里写明原因（铁律新 16）。
    "data": {"quality": {"enabled": True, "strict": False,
                         "max_symbols": 200}},
}


class ConfigSourceError(RuntimeError):
    """配置源读取/解析失败（YAML 语法、文件不可读、顶层非映射等）。"""


def deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """就地深合并（overlay 覆盖 base；非 dict 值整替；返回 base 引用）。"""
    for k, v in overlay.items():
        if isinstance(v, Mapping) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def _coerce(raw: str) -> Any:
    """环境变量字符串 → 类型推断值（bool/int/float/str）。"""
    low = raw.strip().lower()
    if low in {"true", "false"}:
        return low == "true"
    if _INT_RE.match(raw.strip()):
        return int(raw)
    if _FLOAT_RE.match(raw.strip()):
        return float(raw)
    return raw


def from_env(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """L2：BTF_<SECTION>__<KEY> → 嵌套 dict（空值视为未设置）。"""
    env = environ if environ is not None else os.environ
    out: dict[str, Any] = {}
    for key, raw in env.items():
        if not key.startswith("BTF_") or key in _PATH_ENV or raw == "":
            continue
        path = [p.lower() for p in key[len("BTF_"):].split("__") if p]
        if not path:
            continue
        node = out
        for part in path[:-1]:
            nxt = node.get(part)
            node[part] = nxt if isinstance(nxt, dict) else {}
            node = node[part]
        node[path[-1]] = _coerce(raw)
    return out


def load_yaml(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """L3：YAML 文件/文本 → 映射（safe_load；顶层非映射显式报错）。

    Mapping 直接透传（运行器内存态配置——同一 Schema 管辖）。
    """
    if isinstance(source, Mapping):
        return copy.deepcopy(dict(source))
    import yaml

    # str 两义（文本/路径）：含换行 → 文本；无换行且文件存在 → 路径；
    # 否则按文本解析（单行 YAML 少见，误判时 yaml 报错信息亦友好）
    if isinstance(source, str) and ("\n" in source or not Path(source).exists()):
        text: str | None = source
    else:
        text = None
    if text is None:
        path = Path(source)                       # type: ignore[arg-type]
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigSourceError(f"配置文件不可读：{path}（{exc}）") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigSourceError(f"YAML 解析失败：{exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigSourceError(
            f"YAML 顶层必须为映射，实际 {type(data).__name__}")
    return data


def load_config(
    source: str | Path | Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """四层合并（默认 → 环境变量 → 用户 YAML → CLI 覆盖）→ 未校验配置。

    校验由 validation.validate() 独立执行（调用方决定失败策略）。
    """
    cfg: dict[str, Any] = copy.deepcopy(DEFAULTS)
    deep_merge(cfg, from_env(environ))
    if source is not None:
        deep_merge(cfg, load_yaml(source))
    if overrides:
        deep_merge(cfg, overrides)
    return cfg


__all__ = [
    "DEFAULTS",
    "ConfigSourceError",
    "deep_merge",
    "from_env",
    "load_config",
    "load_yaml",
]
