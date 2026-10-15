#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/data_backup.py — 全量数据冷备清单与校验（DB3；对齐 btf cold_backup 形态）
====================================================================
背景：`D:\\全量数据\\`（主库 market_data + alt_data，约 6.6 GB）此前无定期冷备/校验工具
（2026-09-29 DB3 登记；2026-10-15 老大决策执行）。

本工具 =「清单 + 校验」轻量版（不动数据、不写数据根）：
  --make-manifest   扫描数据根 → 产出 MANIFEST.json（逐文件 sha256/size/mtime）
                    + SHA256SUMS.txt（sha256sum -c 兼容）+ README（含镜像命令）
  --verify          按 MANIFEST 复算比对（缺件 / 大小变 / 哈希变 → 逐条报出）
镜像（robocopy）为手动动作，命令与"对源还是对镜像校验"的用法见产出的 README。

纪律（对齐 btf cold_backup）：
  · 只读数据根（不写、不删、不改）；排除派生残留（``.tmp`` 等）；
  · **fail-closed**：任一文件读取/哈希失败 → 不产出、退出非零；
  · verify 对"镜像副本"跑才有长期意义——对源跑仅在同一批次时刻有效
    （数据根按日更新，隔日对源 verify 会"报正常更新"，属预期）。
独立脚本，不注册进 main.py（clean_scratch 先例：不动 CLI 契约）。

用法：
    python tools/data_backup.py --make-manifest
    python tools/data_backup.py --make-manifest --root "D:\\全量数据" --out "D:\\量化策略\\备份\\data-backup" --reason "首次基线"
    python tools/data_backup.py --verify "D:\\量化策略\\备份\\data-backup\\MANIFEST-20261015-0835.json"
    python tools/data_backup.py --verify <MANIFEST.json> --root "<镜像目录>"   # 校验镜像副本
退出码：0=成功 / 1=失败
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path(r"D:\全量数据")
DEFAULT_OUT = Path(r"D:\量化策略\备份\data-backup")

EXCLUDE_DIRS = {"__pycache__", ".git"}
EXCLUDE_SUFFIX = {".tmp", ".pyc", ".lock"}


def _iter_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for item in sorted(root.rglob("*")):
        if item.is_dir():
            continue
        if any(part in EXCLUDE_DIRS for part in item.parts):
            continue
        if item.suffix in EXCLUDE_SUFFIX:
            continue
        out.append(item)
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_manifest(root: Path, out_dir: Path, reason: str) -> int:
    if not root.is_dir():
        print(f"[FAIL] 数据根不存在: {root}")
        return 1
    files = _iter_files(root)
    total = len(files)
    print(f"[i] 数据根: {root} | 文件数: {total}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")

    entries: list[dict] = []
    total_bytes = 0
    t0 = time.time()
    for i, fp in enumerate(files, 1):
        rel = fp.relative_to(root).as_posix()
        try:
            st = fp.stat()
            sha = _sha256(fp)
        except Exception as e:  # fail-closed
            print(f"[FAIL] 读取/哈希失败: {rel} :: {e}")
            return 1
        entries.append({
            "path": rel,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "sha256": sha,
        })
        total_bytes += st.st_size
        if i % 500 == 0 or i == total:
            gb = total_bytes / 1024**3
            el = time.time() - t0
            print(f"[i] {i}/{total} | {gb:.2f} GB | {el:.0f}s")

    manifest = {
        "root": str(root),
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": reason,
        "total_files": total,
        "total_bytes": total_bytes,
        "files": entries,
    }
    mpath = out_dir / f"MANIFEST-{stamp}.json"
    with io.open(mpath, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    spath = out_dir / f"SHA256SUMS-{stamp}.txt"
    with io.open(spath, "w", encoding="utf-8", newline="\n") as f:
        for e in entries:
            f.write(f"{e['sha256']}  {e['path']}\n")

    rpath = out_dir / f"README-{stamp}.md"
    with io.open(rpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            f"# 全量数据冷备清单（{stamp}）\n\n"
            f"- 数据根：`{root}`\n"
            f"- 文件数：{total} ｜ 总字节：{total_bytes:,}（{total_bytes/1024**3:.2f} GB）\n"
            f"- 依据：{reason}\n\n"
            "## 用法\n\n"
            "1) **镜像（手动，robocopy）**——示例（镜像到 E 盘 `E:\\全量数据-mirror`）：\n\n"
            "```bat\n"
            "robocopy \"D:\\全量数据\" \"E:\\全量数据-mirror\" /MIR /R:1 /W:1 /NP /TEE /LOG:\"D:\\量化策略\\备份\\data-backup\\mirror.log\"\n"
            "```\n\n"
            "2) **校验镜像副本**（长期有效）：\n\n"
            "```bash\n"
            "python tools/data_backup.py --verify <本目录 MANIFEST-*.json> --root \"E:\\全量数据-mirror\"\n"
            "```\n\n"
            "3) **对源即时自检**（同一批次时刻有效；隔日后对源会报「正常更新」，属预期）：\n\n"
            "```bash\n"
            "python tools/data_backup.py --verify <MANIFEST-*.json>\n"
            "```\n\n"
            "> 说明：`SHA256SUMS-*.txt` 与清单同源，可 `sha256sum -c` 使用（相对路径）。\n"
        )

    print(f"[OK] MANIFEST: {mpath}")
    print(f"[OK] SHA256SUMS: {spath}")
    print(f"[OK] README: {rpath}")
    print(f"[OK] 共 {total} 文件 / {total_bytes/1024**3:.2f} GB / 耗时 {time.time()-t0:.0f}s")
    return 0


def verify(manifest_path: Path, root_override: Path | None) -> int:
    if not manifest_path.is_file():
        print(f"[FAIL] MANIFEST 不存在: {manifest_path}")
        return 1
    with io.open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    root = root_override or Path(manifest["root"])
    print(f"[i] 校验: {manifest_path.name} | 根: {root} | 文件数: {manifest['total_files']}")
    missing, changed, ok = [], [], 0
    t0 = time.time()
    for i, e in enumerate(manifest["files"], 1):
        fp = root / e["path"]
        if not fp.is_file():
            missing.append(e["path"])
            continue
        try:
            st = fp.stat()
            if st.st_size != e["size"]:
                changed.append(f"{e['path']} | size {e['size']} -> {st.st_size}")
                continue
            sha = _sha256(fp)
            if sha != e["sha256"]:
                changed.append(f"{e['path']} | sha256 mismatch")
            else:
                ok += 1
        except Exception as ex:
            changed.append(f"{e['path']} | 读取失败: {ex}")
        if i % 500 == 0 or i == len(manifest["files"]):
            print(f"[i] {i}/{len(manifest['files'])} | ok={ok} | el={time.time()-t0:.0f}s")

    print(f"[i] 结果: OK {ok} | 差异 {len(changed)} | 缺失 {len(missing)}")
    for x in changed[:50]:
        print(f"  [DIFF] {x}")
    for x in missing[:50]:
        print(f"  [MISS] {x}")
    if changed or missing:
        print("[FAIL] 校验不完全通过")
        return 1
    print("[OK] 校验全部通过")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="全量数据冷备清单与校验（DB3）")
    parser.add_argument("--make-manifest", action="store_true", help="生成清单")
    parser.add_argument("--verify", metavar="MANIFEST", help="按清单校验")
    parser.add_argument("--root", default=None, help=f"数据根（默认 {DEFAULT_ROOT}；verify 时可指向镜像目录）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="清单输出目录")
    parser.add_argument("--reason", default="手动清单（DB3 轻量版）", help="清单依据")
    args = parser.parse_args(argv)

    if args.make_manifest:
        root = Path(args.root) if args.root else DEFAULT_ROOT
        return make_manifest(root, Path(args.out), args.reason)
    if args.verify:
        return verify(Path(args.verify), Path(args.root) if args.root else None)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
