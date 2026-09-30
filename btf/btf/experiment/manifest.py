# -*- coding: utf-8 -*-
"""RunManifest：四版本锁定 + 规则版本（08 §13.2；M2 任务 5.5）。

四版本（可复现性 R1 的锁定对象）：
    data_version    数据锚点（dataset/anchor_date/content_hash/tables/completeness_level）
    code_version    git_commit + dirty + package_version
    config_hash     合并后冻结配置哈希（config_effective 脱敏前的规范 JSON）
    env_version     运行时实采（E2 纪律：importlib.metadata 实测，不抄录文档）
外加（H2 规则单一真源）：
    rules_version / rules_overrides

落盘纪律（08 §13.2）：manifest **启动先写骨架（status=RUNNING），结束补全**
——崩溃的 run 也能被审计（对照断点三态纪律）。

**指纹预算留痕**：``data_version.content_hash`` 默认取配置声明的
``expected_hash``（缺失则 "sha256:unknown"）——run 启动不做全库指纹实测
（DataVersionFingerprint 全库扫描与 B3 性能预算冲突），实测注入归 5.6
黄金集/CLI dataset 命令。
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any

from btf._version import __version__  # 不经包根（import-linter 假链，见 _version.py）
from btf.config.mask import mask_config
from btf.domain.contracts import CONTRACT_VERSION

#: run 数据契约版本（04 §8.7：落盘方写入 manifest，读取器向后兼容）
RUNRESULT_CONTRACT = "runresult.v1"

#: run 状态（骨架 → 终态；终态二选一）
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"

#: env_version 实采的第三方库（只查已安装版本，不 import——E2 纪律）
_ENV_LIBS = ("numpy", "pandas", "pyarrow")


def _major(version: str) -> str:
    """契约版本主段（`runresult.v1` → `v1`；无点号则整体）。"""
    return version.rsplit(".", 1)[-1]


# ─────────────────────────────────────────────────────────────
# 规范序列化与哈希（R1 复现锚点：同输入 → 同字符串 → 同哈希）
# ─────────────────────────────────────────────────────────────
def canonical_json(obj: Any) -> str:
    """规范 JSON（键排序 + 紧凑分隔；确保跨进程同字节）。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def sha256_hex(text: str, *, length: int | None = None) -> str:
    """sha256 指纹（``sha256:<hex>``，可截断）。"""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:length] if length else digest}"


def config_hash_of(config: Mapping[str, Any]) -> str:
    """合并后冻结配置的哈希（08 §13.2 config_hash）。"""
    return sha256_hex(canonical_json(config))


def metrics_digest_of(metrics: Mapping[str, float]) -> str:
    """指标摘要哈希（R1 校验锚，08 §13.2 metrics_digest）。

    确定性：键名排序 + 浮点统一 17 位有效精度（repr 等价），NaN 归一。
    """
    parts = []
    for name in sorted(metrics):
        value = metrics[name]
        text = "nan" if value != value else f"{float(value):.17g}"
        parts.append(f"{name}={text}")
    return sha256_hex("\n".join(parts))


# ─────────────────────────────────────────────────────────────
# 版本采集
# ─────────────────────────────────────────────────────────────
def collect_code_version() -> dict[str, Any]:
    """代码版本：git HEAD + 工作区脏标志 + 包版本（失败降级 unknown）。"""
    commit, dirty = "unknown", False
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=5, check=False,
        )
        if head.returncode == 0 and head.stdout.strip():
            commit = head.stdout.strip()[:12]
            status = subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True,
                text=True, timeout=10, check=False,
            )
            dirty = bool(status.returncode == 0 and status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = "unknown", False
    try:
        package = f"btf=={pkg_version('btf')}"
    except PackageNotFoundError:
        package = f"btf=={__version__}"      # 未安装（源码直跑）回退包内版本
    return {"git_commit": commit, "dirty": dirty, "package_version": package}


def collect_env_version() -> dict[str, Any]:
    """环境版本：python/三方库/platform（运行时实采，不抄录文档——E2）。"""
    libs: dict[str, str] = {}
    for lib in _ENV_LIBS:
        try:
            libs[lib] = pkg_version(lib)
        except PackageNotFoundError:
            continue        # 未安装即不登记（不伪造）
    return {"python": platform.python_version(), "libs": libs,
            "platform": sys.platform}


def collect_data_version(config: Mapping[str, Any],
                         fingerprint: str | None = None) -> dict[str, Any]:
    """数据版本：配置声明 + 可选实测指纹注入。"""
    data_cfg = config.get("data") or {}
    return {
        "dataset": data_cfg.get("feed", "unknown"),
        "anchor_date": data_cfg.get("anchor_date"),
        "content_hash": fingerprint or data_cfg.get("expected_hash")
                        or "sha256:unknown",
        "tables": list(data_cfg.get("tables") or []),
        "completeness_level": data_cfg.get("completeness_level", "L0"),
    }


def _merge_data_version(config: Mapping[str, Any],
                        fingerprint: str | None,
                        override: Mapping[str, Any] | None) -> dict[str, Any]:
    """数据版本 = 配置/指纹回退 + **实测覆盖**（BB-1，19 号 §39.4 重路）。

    原 `data_version` 恒为占位（`content_hash="sha256:unknown"`）——**空洞披露**
    （P2-NEW-7 / 铁律新 16）：接口留了、读口接了、写口从未发生。现 runtime 在
    装配期用 `data/version.py::compute()` **实算**并把真值经本参数传入。
    """
    out = collect_data_version(config, fingerprint)
    if override:
        out.update({k: v for k, v in override.items() if v is not None})
    return out


def collect_seed(config: Mapping[str, Any]) -> dict[str, Any]:
    """种子（08 §13.4：未设默认 0——调用方按策略是否含随机性决定是否告警）。"""
    seed = config.get("seed")
    master = seed if isinstance(seed, int) else (
        seed.get("master", 0) if isinstance(seed, Mapping) else 0)
    return {"master": master, "derived": {}}


def new_run_id(config_hash: str, now: datetime | None = None) -> str:
    """run_id = 时间戳 + config_hash 前 6 位（08 §13.2）。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    short = config_hash.split(":", 1)[-1][:6]
    return f"{stamp}_{short}"


# ─────────────────────────────────────────────────────────────
# RunManifest
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RunManifest:
    """run 清单（08 §13.2 Schema + contract_version + 崩溃审计字段）。"""

    run_id: str
    status: str = STATUS_RUNNING
    contract_version: str = RUNRESULT_CONTRACT
    btf_contract_version: str = CONTRACT_VERSION
    data_version: Mapping[str, Any] = field(default_factory=dict)
    code_version: Mapping[str, Any] = field(default_factory=dict)
    config_hash: str = ""
    config_effective: Mapping[str, Any] = field(default_factory=dict)
    env_version: Mapping[str, Any] = field(default_factory=dict)
    rules_version: str = "none"
    rules_overrides: Sequence[Mapping[str, Any]] = ()
    seed: Mapping[str, Any] = field(default_factory=dict)
    metrics_digest: str | None = None
    error: str | None = None
    #: 装配期口径披露（P1-NEW-5，19 号 §22.2）：如 `source=all` 宇宙期间并集
    #: 「跨期新增 N 只」——**宇宙口径是回测结论的前提**（5,690 只机会集与
    #: 5,067 只不是同一个结论，跨区间/跨实验不可比），故必须随产物落盘、
    #: 由报告披露区渲染（与 `degraded_notes` 对称）；空元组 = 无披露项。
    assembly_notes: tuple[str, ...] = ()

    # ── 生命周期 ──
    @classmethod
    def skeleton(
        cls,
        config: Mapping[str, Any],
        *,
        run_id: str | None = None,
        data_fingerprint: str | None = None,
        rules_version: str = "none",
        rules_overrides: Sequence[Mapping[str, Any]] = (),
        assembly_notes: Sequence[str] = (),
        data_version: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> RunManifest:
        """启动期骨架（status=RUNNING，metrics_digest=None——崩溃可审计）。"""
        cfg_hash = config_hash_of(config)
        return cls(
            run_id=run_id or new_run_id(cfg_hash, now),
            status=STATUS_RUNNING,
            data_version=_merge_data_version(config, data_fingerprint,
                                             data_version),
            code_version=collect_code_version(),
            config_hash=cfg_hash,
            config_effective=mask_config(config),
            env_version=collect_env_version(),
            rules_version=rules_version,
            rules_overrides=tuple(rules_overrides),
            seed=collect_seed(config),
            assembly_notes=tuple(assembly_notes),
        )

    def completed(self, metrics: Mapping[str, float]) -> RunManifest:
        """结束期补全（status=COMPLETED + metrics_digest=R1 锚）。"""
        return replace(self, status=STATUS_COMPLETED,
                       metrics_digest=metrics_digest_of(metrics))

    def failed(self, reason: str) -> RunManifest:
        """失败终态（骨架已落盘——崩溃 run 仍可审计）。"""
        return replace(self, status=STATUS_FAILED, error=reason)

    # ── 序列化 ──
    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "contract_version": self.contract_version,
            "btf_contract_version": self.btf_contract_version,
            "data_version": dict(self.data_version),
            "code_version": dict(self.code_version),
            "config_hash": self.config_hash,
            "config_effective": dict(self.config_effective),
            "env_version": dict(self.env_version),
            "rules_version": self.rules_version,
            "rules_overrides": [dict(o) for o in self.rules_overrides],
            "seed": dict(self.seed),
            "metrics_digest": self.metrics_digest,
            "error": self.error,
            "assembly_notes": list(self.assembly_notes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunManifest:
        """反序列化（**读取端版本兼容分支**，P2-1b / 19 号附录 D.3）。

        原实现只用 `.get(默认值)` 兜键——docstring 声称"按 contract_version
        兼容"但**无任何版本判定**：读 v2 产物时把不认识的结构当 v1 解析，
        静默产出错值（与"声明 vs 事实不符"同族）。
        今：主版本不同即显式拒绝（fail-closed）；字段级新增仍靠默认值兜底
        （次版本向前兼容）。
        """
        version = str(data.get("contract_version") or RUNRESULT_CONTRACT)
        if _major(version) != _major(RUNRESULT_CONTRACT):
            raise ValueError(
                f"manifest contract_version={version!r} 与本读取器不兼容"
                f"（支持主版本 {RUNRESULT_CONTRACT!r}）——拒绝解析以免静默错值")
        return cls(
            run_id=data["run_id"], status=data.get("status", STATUS_RUNNING),
            contract_version=version,
            btf_contract_version=data.get("btf_contract_version", CONTRACT_VERSION),
            data_version=data.get("data_version") or {},
            code_version=data.get("code_version") or {},
            config_hash=data.get("config_hash", ""),
            config_effective=data.get("config_effective") or {},
            env_version=data.get("env_version") or {},
            rules_version=data.get("rules_version", "none"),
            rules_overrides=tuple(data.get("rules_overrides") or ()),
            seed=data.get("seed") or {},
            metrics_digest=data.get("metrics_digest"),
            error=data.get("error"),
            # 向后兼容：旧产物无该键 → 空元组（读取器不因缺键失败）
            assembly_notes=tuple(data.get("assembly_notes") or ()),
        )


__all__ = [
    "RUNRESULT_CONTRACT",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_RUNNING",
    "RunManifest",
    "canonical_json",
    "collect_code_version",
    "collect_data_version",
    "collect_env_version",
    "collect_seed",
    "config_hash_of",
    "metrics_digest_of",
    "new_run_id",
    "sha256_hex",
]
