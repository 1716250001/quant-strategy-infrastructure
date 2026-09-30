# -*- coding: utf-8 -*-
"""S1 契约版本与协商（04 §8.7 接口版本与兼容策略；M1 任务 4.1）。

SemVer 纪律（04 §8.7）：
    - S1 契约（Protocol、数据契约、事件类型、RunResult、配置 Schema）
      破坏性变更走 ADR + ≥1 个 minor 版本废弃期；
    - 插件实现声明 ``contract_version: ClassVar[str]``，registry 加载时
      协商：**主版本不符 → 拒绝加载**（不静默降级）；次/补丁差异放行；
    - 数据契约版本（runresult.v1 等）由落盘方写入 manifest，读取器
      向后兼容读取旧版本（只读场景），写出永远是当前版本。

本模块位于 domain（零第三方依赖最内层）——全部层可引用的版本单一真源。
"""
from __future__ import annotations

#: S1 协议集当前版本（M1 契约定稿基线）
CONTRACT_VERSION = "1.0"


class ContractVersionError(RuntimeError):
    """插件契约版本与宿主不兼容（主版本不符 / 未声明 / 非法格式）。"""


def major_of(version: str) -> int:
    """版本串 → 主版本号（非法格式即契约违例，显式报错）。"""
    head = version.split(".")[0]
    try:
        return int(head)
    except ValueError as exc:
        raise ContractVersionError(
            f"非法契约版本号 {version!r}（期望 SemVer 'MAJOR.minor.patch'）") from exc


def negotiate(plugin_name: str, declared: str | None,
              host: str = CONTRACT_VERSION) -> None:
    """版本协商（registry 加载插件时调用，04 §8.7）。

    - 未声明 / 主版本不符 → ContractVersionError（拒载，不静默降级）；
    - 主版本相符（次/补丁任意）→ 放行。
    """
    if not declared:
        raise ContractVersionError(
            f"插件 {plugin_name!r} 未声明 contract_version（S1 契约要求，04 §8.7）")
    if major_of(declared) != major_of(host):
        raise ContractVersionError(
            f"插件 {plugin_name!r} 契约版本 {declared!r} 与宿主 {host!r} "
            f"主版本不符——拒绝加载（请适配 {major_of(host)}.x 契约或升级 btf）")


__all__ = ["CONTRACT_VERSION", "ContractVersionError", "major_of", "negotiate"]
