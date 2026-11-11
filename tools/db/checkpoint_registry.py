# -*- coding: utf-8 -*-
"""tools/db/checkpoint_registry.py — 断点登记门面（P3-2）
=========================================================
`config/checkpoint_registry.py` 是**纯声明**（40 个域，无 IO）；
本模块是它唯一的**读写门面**：谁要读断点、谁要拿时间戳、谁要描述域，
一律从这里走，不得各自 `json.load` 猜路径。

为什么要有门面（18 号 §408 / 派单-P3-2 §三）
--------------------------------------------
2026-11-11 实证：同一条「断点完成度」被 3 个模块用 3 种路径拼法读取
（audit 只认 3 个文件、ckpt_tool 自己拼 MARKET_DATA_DIR、alt 侧又是另一套），
于是 21 个载体里 18 个长期无人核对。门面的职责只有一条：
**把「域 → 物理路径 → 字段」的映射收在不变量处，且不存在域必须当场报错**。

四个公开方法
------------
get(domain_id)                 域声明；不存在 → KeyError（附最接近的 3 个候选）
timestamp(domain_id, table)    该域（可指定表）的时间戳字段值；无则 None
describe(domain_id=None)       不传＝全量聚合；传＝单域详情（含文件是否存在/mtime）
domains_of(library, authority)  该库该等级的域 id 列表

只读纪律
--------
本模块**没有写方法**（没有 set/mark/patch）。P3-2 只做门面，不改变任何写入路径；
写侧仍由各 owner 模块负责（见声明里的 owner 字段）。
"""
from __future__ import annotations

import difflib
import json
import os
from datetime import datetime

from config import ALT_DATA_DIR, MARKET_DATA_DIR
from config import checkpoint_registry as _cfg

__all__ = ["describe", "domains_of", "get", "timestamp"]


def _lib_root(library):
    """库名 → 库根目录（只认两库；其余当场报错，避免拼出错路径）。"""
    if library == "market_data":
        return MARKET_DATA_DIR
    if library == "alt_data":
        return ALT_DATA_DIR
    raise KeyError(f"未知 library: {library!r}（应为 market_data | alt_data）")


def _read_json(path):
    """读 JSON；文件缺失/损坏一律返回 None（只读容错，不影响调用方）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def get(domain_id):
    """取域声明；不存在 → KeyError（带最接近候选，便于修 typo）。"""
    try:
        return _cfg.domain(domain_id)
    except KeyError:
        near = difflib.get_close_matches(
            str(domain_id), [d.id for d in _cfg.all_domains()], n=3)
        raise KeyError(
            f"未登记的断点域: {domain_id!r}；最接近: {near or '无'}"
        ) from None


def domains_of(library, authority="PRIMARY"):
    """该库该权威等级的域 id 列表（默认只看 PRIMARY）。"""
    return _cfg.domains_of(library, authority)


def timestamp(domain_id, table=None):
    """该域最近一次写入时间（ISO 字符串）；读不到/无该字段 → None。

    table=None 时取文件顶层字段（如 rotate_state 的 last_run）；
    指定 table 时取该表记录里的字段（如 alt 的 updated）。
    """
    d = get(domain_id)
    if not d.timestamp_field:
        return None
    data = _read_json(os.path.join(_lib_root(d.library), d.file))
    if not isinstance(data, dict):
        return None
    if table is None:
        value = data.get(d.timestamp_field)
    else:
        rec = data.get(table)
        value = rec.get(d.timestamp_field) if isinstance(rec, dict) else None
    return None if value is None else str(value)


def describe(domain_id=None):
    """不传：全量聚合；传：单域详情（含文件是否真实存在与 mtime）。"""
    if domain_id is None:
        out = _cfg.describe()
        by_lib, by_ks, n_triples = {}, {}, 0
        for d in _cfg.all_domains():
            by_lib[d.library] = by_lib.get(d.library, 0) + 1
            if d.authority == "PRIMARY":
                by_ks[str(d.key_space)] = by_ks.get(str(d.key_space), 0) + len(d.tables)
                n_triples += len(d.tables)
        # K1 要求 by_library 为「库 → 域数」扁平计数（18 + 22 = 40）；
        # config 版给的是嵌套结构，此处按门面契约改扁平，原始结构不再透出。
        out["by_library"] = dict(sorted(by_lib.items()))
        out["by_key_space"] = dict(sorted(by_ks.items()))
        out["primary_triples"] = n_triples
        return out

    d = get(domain_id)
    path = os.path.join(_lib_root(d.library), d.file)
    info = {
        "id": d.id,
        "library": d.library,
        "owner": d.owner,
        "file": d.file,
        "path": path,
        "file_exists": os.path.exists(path),
        "size": None,
        "file_mtime": None,
        "authority": d.authority,
        "key_space": d.key_space,
        "completed_field": d.completed_field,
        "timestamp_field": d.timestamp_field,
        "n_tables": len(d.tables),
        "tables": sorted(d.tables),
        "note": d.note,
    }
    if info["file_exists"]:
        st = os.stat(path)
        info["size"] = st.st_size
        info["file_mtime"] = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    return info
