# -*- coding: utf-8 -*-
"""
tools/db_fingerprint.py — L1 库指纹门禁（全文件类型）
====================================================
背景（§297 G1 / §301 CC-LEGACY-A）:
  旧 L1 门禁只扫 `*.parquet`，于是 2026-11-03 F4 改断点、日更改 meta 时
  门禁**完全没看见** —— `json`/`checkpoint`/`.bak` 全是盲区。
  本工具把**全库树**（`D:\全量数据`，含 market_data / alt_data / derived_data /
  wind_data 等）下**所有文件**纳入指纹，按三类分账:

    parquet  : 数据分区（*.parquet）
    state    : 状态/清单（隐藏文件 + *.json/*.jsonl/*.csv/*.txt）
    other    : 其它（.bak-* / .schema-* 等历史副本）

两档校验:
    快速档（默认）: 比 size + mtime_ns，亚秒级，适合日更后随手跑
    深度档（--deep）: 额外重算 sha256，用于月度/换机/可疑变更复核

用法:
  python tools/db_fingerprint.py --build                 # 建基线（含 sha256）
  python tools/db_fingerprint.py --verify                # 快速档校验
  python tools/db_fingerprint.py --verify --deep          # 深度档校验
  python tools/db_fingerprint.py --verify --allow-added   # 容忍新增文件（报告仍打印）

退出码: 0 = 零差异 / 1 = 有差异 / 2 = 用法或 IO 错误。
⚠ 不接 regress（§297 明确要求）—— 门禁由人工/定时调用，避免与回归互相绑架。
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime

# 2026-11-04 §303 G1b：范围从 market_data 扩到**全库树**；基线移出 root（防自指）
DEFAULT_ROOT = r"D:\全量数据"
DEFAULT_BASELINE = r"D:\量化策略\备份\l1_full_baseline.json"
STATE_EXTS = {".json", ".jsonl", ".csv", ".txt"}


def _kind(path):
    """三类分账: parquet / state / other"""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if ext == ".parquet":
        return "parquet"
    if name.startswith(".") or ext in STATE_EXTS:
        return "state"
    return "other"


def _sha256(path, blocksize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(blocksize)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _walk(root, skip=None):
    """按相对路径排序返回 [(rel, abs)]，保证基线可复现。

    skip: 需排除的绝对路径（基线自身）。基线若落在 root 内，不能把上次
    基线算进本次指纹 —— 否则每次重建都自我引用、永远报"变更"（§303 3.2）。
    """
    skip_n = os.path.normcase(os.path.abspath(skip)) if skip else None
    out = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            if skip_n and os.path.normcase(os.path.abspath(p)) == skip_n:
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            out.append((rel, p))
    out.sort()
    return out


def _atomic_write_json(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _scan(root, deep, skip=None):
    """扫描一遍，返回 {rel: 记录} 与三分类计数、两档耗时"""
    t0 = time.time()
    files = _walk(root, skip=skip)
    t_walk = time.time() - t0
    recs, counts = {}, {"parquet": 0, "state": 0, "other": 0}
    t_hash = 0.0
    for rel, p in files:
        st = os.stat(p)
        k = _kind(p)
        counts[k] += 1
        rec = {"k": k, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        if deep:
            th = time.time()
            rec["sha256"] = _sha256(p)
            t_hash += time.time() - th
        recs[rel] = rec
    return recs, counts, {"walk_s": round(t_walk, 3), "hash_s": round(t_hash, 1),
                          "total_s": round(time.time() - t0, 1)}


def cmd_build(args):
    root = args.root
    if not os.path.isdir(root):
        print(f"[门禁] ⛔ 根目录不存在: {root}")
        return 2
    print(f"[门禁] 建基线 root={root} deep=True")
    recs, counts, t = _scan(root, deep=True, skip=args.baseline)
    base = {"root": root, "created_at": datetime.now().isoformat(timespec="seconds"),
            "deep": True, "counts": counts, "files": recs}
    _atomic_write_json(base, args.baseline)
    total = sum(counts.values())
    print(f"[门禁] 文件数三分类: parquet={counts['parquet']} state={counts['state']} "
          f"other={counts['other']} 合计={total}")
    print(f"[门禁] 两档耗时: 扫描={t['walk_s']}s  sha256={t['hash_s']}s  合计={t['total_s']}s")
    print(f"[门禁] 基线已写: {args.baseline} ({os.path.getsize(args.baseline)/1024:.0f} KB)")
    return 0


def cmd_verify(args):
    if not os.path.exists(args.baseline):
        print(f"[门禁] ⛔ 基线不存在: {args.baseline}（先跑 --build）")
        return 2
    with open(args.baseline, encoding="utf-8") as f:
        base = json.load(f)
    old = base.get("files", {})
    deep = args.deep
    print(f"[门禁] 校验 root={args.root} 档位={'深度(sha256)' if deep else '快速(size+mtime)'} "
          f"基线={base.get('created_at')}")
    cur, counts, t = _scan(args.root, deep=deep, skip=args.baseline)

    added = sorted(set(cur) - set(old))
    removed = sorted(set(old) - set(cur))
    changed = []
    for rel in sorted(set(cur) & set(old)):
        a, b = old[rel], cur[rel]
        if a.get("size") != b["size"] or a.get("mtime_ns") != b["mtime_ns"]:
            changed.append((rel, "size/mtime"))
        elif deep and a.get("sha256") and a["sha256"] != b.get("sha256"):
            changed.append((rel, "sha256"))

    total = sum(counts.values())
    print(f"[门禁] 文件数三分类: parquet={counts['parquet']} state={counts['state']} "
          f"other={counts['other']} 合计={total}")
    print(f"[门禁] 两档耗时: 扫描={t['walk_s']}s"
          + (f"  sha256={t['hash_s']}s" if deep else "  (快速档未算 sha256)")
          + f"  合计={t['total_s']}s")
    print(f"[门禁] 新增={len(added)} 删除={len(removed)} 变更={len(changed)}")
    for rel in added[:20]:
        print(f"  + [{cur[rel]['k']}] {rel}")
    for rel in removed[:20]:
        print(f"  - [{old[rel].get('k')}] {rel}")
    for rel, why in changed[:20]:
        print(f"  ~ [{why}] {rel}")

    bad = bool(removed or changed or (added and not args.allow_added))
    if bad:
        print("[门禁] ⛔ 有差异（退出码 1）")
        return 1
    if added:
        # §303 3.3：数与话必须一致 —— 容忍新增时不说"零差异"
        print(f"[门禁] ✓ 无删除/变更（新增 {len(added)} 已容忍）")
    else:
        print("[门禁] ✓ 零差异（退出码 0）")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="L1 库指纹门禁（全文件类型）")
    ap.add_argument("--build", action="store_true", help="生成/覆盖基线")
    ap.add_argument("--verify", action="store_true", help="校验当前库")
    ap.add_argument("--deep", action="store_true", help="深度档：重算 sha256")
    ap.add_argument("--allow-added", action="store_true", help="--verify 时容忍新增文件")
    ap.add_argument("--root", default=DEFAULT_ROOT, help=f"库根目录（默认 {DEFAULT_ROOT}）")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE,
                    help=f"基线路径（默认 {DEFAULT_BASELINE}）")
    args = ap.parse_args(argv)
    if args.build and args.verify:
        print("[门禁] ⛔ --build 与 --verify 不能同时使用")
        return 2
    if args.build:
        return cmd_build(args)
    if args.verify:
        return cmd_verify(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
