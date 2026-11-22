# -*- coding: utf-8 -*-
"""known_gaps.json 非 fund 表残余缺口补丁（G2 尾单 T1/T4）。

默认干跑；``--apply`` 才写盘，写前把目标文件复制到同目录
``.bak-known-gaps-<YYYYmmdd_HHMMSS>``。写入走「临时文件 + fsync + os.replace」。

补丁 JSON 结构（``mode=additive-only``）::

    {"patch": {"<table>": {
        "source_empty_ranges": [{"code", "from", "to", "keys"}],
        "source_empty_evidence": {"<code>": {"probe_rows", "probe_date", "probe_source"}},
        "note": "...",
        "real_gap_backfill": [{"code", "keys", "from", "to"}]}}}

合并语义：已有表 dict 保留全部原字段；range 按 (code, from, to) 去重追加；
evidence 逐码合并（既有 probe_rows ≥ 新值时不覆盖）；note 追加；真实缺口整体写入。

用法::

    python tools/db/known_gaps_patch.py --patch <json>          # 干跑
    python tools/db/known_gaps_patch.py --patch <json> --apply  # 写盘（需批准）
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys

from config import tables

CODE_RE = re.compile(r"^\d{6,7}\.(SH|SZ|BJ)$")
DATE_RE = re.compile(r"^\d{8}$")
BACKUP_PREFIX = ".bak-known-gaps-"
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KG_PATH = os.path.join(_HERE, "data", "known_gaps.json")
TABLE_SET = frozenset(tables.DB_AUDIT_PKEYS) | frozenset(tables.TABLE_DATE_COL)


def _require(cond, msg):
    if not cond:
        raise ValueError(msg)


def load_patch(path):
    """读补丁文件并返回 ``patch`` 段（同时校验顶层结构）。"""
    _require(os.path.isfile(path), f"补丁文件不存在：{path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _require(isinstance(data, dict), "补丁顶层必须是 JSON 对象")
    patch = data.get("patch")
    _require(isinstance(patch, dict) and patch, "补丁缺少非空 patch 对象")
    return patch


def _validate_date(value, where):
    _require(
        isinstance(value, str) and bool(DATE_RE.match(value)),
        f"{where} 必须是 8 位日期，实际 {value!r}",
    )
    try:
        dt.datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ValueError(f"{where} 不是合法日期：{value!r}") from None


def _validate_range(item, where):
    _require(isinstance(item, dict), f"{where} 必须是对象")
    code = item.get("code")
    _require(isinstance(code, str) and bool(CODE_RE.match(code)), f"{where}.code 非法：{code!r}")
    _validate_date(item.get("from"), f"{where}.from")
    _validate_date(item.get("to"), f"{where}.to")
    keys = item.get("keys")
    _require(
        isinstance(keys, int) and not isinstance(keys, bool) and keys > 0,
        f"{where}.keys 必须是正整数，实际 {keys!r}",
    )


def validate_patch(patch):
    """校验表名、range、evidence、note、真实缺口结构；返回 patch 本身。"""
    for table, entry in patch.items():
        _require(table in TABLE_SET, f"表名不在 config 表集合内：{table}")
        _require(isinstance(entry, dict), f"{table} 的补丁必须是对象")
        ranges = entry.get("source_empty_ranges", [])
        _require(isinstance(ranges, list), f"{table}.source_empty_ranges 必须是数组")
        for idx, item in enumerate(ranges):
            _validate_range(item, f"{table}.source_empty_ranges[{idx}]")
        evidence = entry.get("source_empty_evidence", {})
        _require(isinstance(evidence, dict), f"{table}.source_empty_evidence 必须是对象")
        for code, ev in evidence.items():
            _require(
                isinstance(code, str) and bool(CODE_RE.match(code)),
                f"{table}.source_empty_evidence 码非法：{code!r}",
            )
            _require(isinstance(ev, dict), f"{table}.{code}.evidence 必须是对象")
            rows = ev.get("probe_rows")
            _require(
                isinstance(rows, int) and not isinstance(rows, bool) and rows >= 0,
                f"{table}.{code}.probe_rows 必须是非负整数，实际 {rows!r}",
            )
        note = entry.get("note")
        _require(note is None or isinstance(note, str), f"{table}.note 必须是字符串")
        real = entry.get("real_gap_backfill", [])
        _require(isinstance(real, list), f"{table}.real_gap_backfill 必须是数组")
        for idx, item in enumerate(real):
            _validate_range(item, f"{table}.real_gap_backfill[{idx}]")
    return patch


def _merge_table(existing, entry):
    """把单表补丁并入已有 dict（原地），返回该表统计。"""
    old_ranges = existing.get("source_empty_ranges")
    if not isinstance(old_ranges, list):
        old_ranges = []
    seen = {(r.get("code"), r.get("from"), r.get("to")) for r in old_ranges if isinstance(r, dict)}
    added_ranges = added_keys = deduped = 0
    for item in entry.get("source_empty_ranges", []):
        key = (item["code"], item["from"], item["to"])
        if key in seen:
            deduped += 1
            continue
        seen.add(key)
        old_ranges.append(dict(item))
        added_ranges += 1
        added_keys += item["keys"]
    existing["source_empty_ranges"] = old_ranges

    evidence = existing.get("source_empty_evidence")
    if evidence is None:
        evidence = {}
    _require(isinstance(evidence, dict), "既有 source_empty_evidence 不是对象，拒绝合并")
    for code, ev in entry.get("source_empty_evidence", {}).items():
        old = evidence.get(code)
        old_rows = old.get("probe_rows", -1) if isinstance(old, dict) else -1
        if old_rows >= ev.get("probe_rows", 0):
            continue
        evidence[code] = dict(ev)
    existing["source_empty_evidence"] = evidence

    note = entry.get("note")
    if note:
        old_note = existing.get("note")
        if not old_note:
            existing["note"] = note
        elif note not in old_note:
            existing["note"] = f"{old_note}\n\n{note}"

    if "real_gap_backfill" in entry:
        existing["real_gap_backfill"] = [dict(x) for x in entry["real_gap_backfill"]]

    real = existing.get("real_gap_backfill") or []
    return {
        "added_ranges": added_ranges,
        "added_keys": added_keys,
        "deduped": deduped,
        "real_gap_ranges": len(real),
        "real_gap_keys": sum(x.get("keys", 0) for x in real),
    }


def merge_known_gaps(current, patch):
    """返回 (合并后的新 dict, 逐表统计)；不修改入参。"""
    merged = json.loads(json.dumps(current, ensure_ascii=False))
    stats = {}
    for table, entry in patch.items():
        existing = merged.get(table)
        if not isinstance(existing, dict):
            existing = {}
            merged[table] = existing
        stats[table] = _merge_table(existing, entry)
    return merged, stats


def _backup_path(kg_path):
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{kg_path}{BACKUP_PREFIX}{stamp}"
    suffix = 1
    while os.path.exists(path):
        path = f"{kg_path}{BACKUP_PREFIX}{stamp}-{suffix}"
        suffix += 1
    return path


def _write_json_atomic(path, payload):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def run(patch_path, kg_path=DEFAULT_KG_PATH, apply=False):
    """校验 + 合并；apply=True 时备份并原子写盘。返回 {stats, backup, applied}。"""
    patch = validate_patch(load_patch(patch_path))
    with open(kg_path, encoding="utf-8") as f:
        current = json.load(f)
    merged, stats = merge_known_gaps(current, patch)
    backup = None
    if apply:
        backup = _backup_path(kg_path)
        shutil.copy2(kg_path, backup)
        _write_json_atomic(kg_path, merged)
    return {"stats": stats, "backup": backup, "applied": apply, "kg_path": kg_path}


def _print_stats(result, patch_path):
    mode = "APPLY" if result["applied"] else "DRY-RUN"
    print(f"[known_gaps_patch] {mode} patch={patch_path}")
    for table, s in result["stats"].items():
        print(
            f"  {table}: 新增 range {s['added_ranges']} / 键 {s['added_keys']:,}；"
            f"去重 {s['deduped']}；真实缺口 {s['real_gap_ranges']} range / "
            f"{s['real_gap_keys']:,} 键"
        )
    if result["backup"]:
        print(f"  备份：{result['backup']}")
        print(f"  已写入：{result['kg_path']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="known_gaps.json 非 fund 表残余缺口补丁")
    parser.add_argument("--patch", required=True, help="补丁 JSON 路径")
    parser.add_argument("--apply", action="store_true", help="写盘（默认干跑）")
    args = parser.parse_args(argv)
    try:
        result = run(args.patch, apply=args.apply)
    except (OSError, ValueError) as exc:
        print(f"[known_gaps_patch] 校验/执行失败：{exc}", file=sys.stderr)
        return 2
    _print_stats(result, args.patch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
