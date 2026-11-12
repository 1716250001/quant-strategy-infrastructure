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
  --mirror          日备（CC-FIX-20 / R10-b，方案 A）：硬链增量快照 →
                    `D:\\全量数据\\_backup\\daily\\YYYYMMDD` ＋ `sha256.jsonl` 校验 →
                    7 版轮转＋周备 4 版（周备从当日整树硬链）
  --package         方案 C：config／known_gaps／表清单 → 本地 tar.xz（不上传）
⚠ **同盘不算备份**：`_backup` 与源同在 D 盘，仅防误删／写坏，**不防盘坏**。

纪律（对齐 btf cold_backup）：
  · 只读数据根（不写、不删、不改）；排除派生残留（``.tmp`` 等）；
  · **fail-closed**：任一文件读取/哈希失败 → 不产出、退出非零；
  · verify 对"镜像副本"跑才有长期意义——对源跑仅在同一批次时刻有效
    （数据根按日更新，隔日对源 verify 会"报正常更新"，属预期）。
已注册为 `db backup` 子命令（R10-1／§427；§428 裁「不注册进 main.py」约束作废，mirror() 本体仍不改）。

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
import json
import os
import shutil
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

import config

DEFAULT_ROOT = Path(config.DATA_ROOT)
DEFAULT_OUT = Path(config.PROJECT_DIR) / "备份" / "data-backup"
MIRROR_ROOT = DEFAULT_ROOT / "_backup"      # 方案 A：与源同盘（仅防误删/写坏，不防盘坏）
KEEP_DAILY, KEEP_WEEKLY = 7, 4

EXCLUDE_DIRS = {"__pycache__", ".git", "_backup"}   # _backup 自身不纳入备份（防递归）
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
    with open(mpath, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    spath = out_dir / f"SHA256SUMS-{stamp}.txt"
    with open(spath, "w", encoding="utf-8", newline="\n") as f:
        for e in entries:
            f.write(f"{e['sha256']}  {e['path']}\n")

    rpath = out_dir / f"README-{stamp}.md"
    with open(rpath, "w", encoding="utf-8", newline="\n") as f:
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
    with open(manifest_path, encoding="utf-8") as f:
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


def _rotate(base: Path, keep: int) -> list[str]:
    """按目录名（日期/周）保留最近 keep 份，返回被滚出的目录名。"""
    dirs = sorted([d for d in base.iterdir() if d.is_dir()]) if base.is_dir() else []
    removed = []
    for d in dirs[:-keep] if len(dirs) > keep else []:
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
    return removed


def _snapshot(src: Path, dst: Path, prev: Path | None) -> tuple[list[dict], dict]:
    """硬链接增量快照：size+mtime 未变 → 硬链上一版（不重读）；否则复制并算 sha。

    ⚠ 同盘硬链接只是省空间，**不改变「同盘不算备份」的事实**：本备份仅防误删/写坏，
      **不防盘坏**（派单 A.2.1，禁自欺）。
    """
    idx: dict[str, dict] = {}
    if prev and (prev / "sha256.jsonl").is_file():
        with open(prev / "sha256.jsonl", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                idx[r["rel"]] = r
    files = _iter_files(src)
    entries, copied, linked = [], 0, 0
    t0 = time.time()
    for i, fp in enumerate(files, 1):
        rel = fp.relative_to(src).as_posix()
        st = fp.stat()
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        old = idx.get(rel)
        if prev and old and old["size"] == st.st_size and old["mtime"] == int(st.st_mtime):
            try:
                os.link(prev / rel, out)
                entries.append({"src": str(fp), "rel": rel, "backup": str(out),
                                "sha256": old["sha256"], "size": st.st_size,
                                "mtime": int(st.st_mtime), "link": True})
                linked += 1
                continue
            except OSError:
                pass
        shutil.copy2(fp, out)
        entries.append({"src": str(fp), "rel": rel, "backup": str(out), "sha256": _sha256(out),
                        "size": st.st_size, "mtime": int(st.st_mtime), "link": False})
        copied += 1
        if i % 2000 == 0 or i == len(files):
            print(f"[i] {i}/{len(files)} | 复制 {copied} 链接 {linked} | {time.time() - t0:.0f}s")
    with open(dst / "sha256.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return entries, {"files": len(files), "copied": copied, "linked": linked,
                     "bytes": sum(e["size"] for e in entries), "elapsed": time.time() - t0}


def _verify_snapshot(entries: list[dict], dst: Path) -> list[str]:
    """F2：清单行数＝源文件数；逐条 size 对齐；复制件重算 sha、硬链件核 inode。"""
    bad = []
    for e in entries:
        p = dst / e["rel"]
        if not p.is_file():
            bad.append(f"MISS {e['rel']}")
        elif p.stat().st_size != e["size"]:
            bad.append(f"SIZE {e['rel']} {p.stat().st_size}!={e['size']}")
        elif not e.get("link") and _sha256(p) != e["sha256"]:
            bad.append(f"SHA {e['rel']}")
    return bad


def _link_tree(src: Path, dst: Path) -> int:
    """整树硬链接（周备从当日快照派生，不复制数据）；非快照元文件也一并链接。"""
    n = 0
    for fp in src.rglob("*"):
        if fp.is_dir():
            continue
        out = dst / fp.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(fp, out)
        except OSError:
            shutil.copy2(fp, out)
        n += 1
    return n


def _unique_bytes(root: Path) -> int:
    """硬链树按 inode 去重后的真实占盘（F5 用；直接 sum(size) 会把硬链重复计入）。"""
    seen: set[tuple[int, int]] = set()
    total = 0
    for fp in root.rglob("*"):
        if not fp.is_file() or "pkg" in fp.relative_to(root).parts[:1]:
            continue
        try:
            st = fp.stat()
        except OSError:
            continue
        key = (st.st_dev, st.st_ino)
        if key in seen:
            continue
        seen.add(key)
        total += st.st_size
    return total


def _write_report(dst: Path, stats: dict, bad: list[str], extra: str = "") -> Path:
    rp = dst / "BACKUP-REPORT.md"
    rp.write_text(
        "# 全库冷备报告\n\n"
        f"- 源：`{DEFAULT_ROOT}`\n- 快照：`{dst}`\n- 依据：CC-FIX-20 / R10-b（方案 A）\n"
        f"- 生成：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "> ⚠ **同盘不算备份**：本快照与源同在 D 盘，仅防误删／写坏，**不防盘坏**。\n\n"
        f"| 项 | 值 |\n|:--|:--|\n"
        f"| 文件数 | {stats['files']} |\n"
        f"| 总字节（逻辑） | {stats['bytes']:,}（{stats['bytes'] / 1024**3:.2f} GB）|\n"
        f"| 去重实际占盘 | {stats.get('unique_bytes', 0):,}"
        f"（{stats.get('unique_bytes', 0) / 1024**3:.2f} GB，源库 {stats.get('ratio', 0):.2f} 倍）|\n"
        f"| 本次复制／硬链 | {stats['copied']}／{stats['linked']} |\n"
        f"| sha256 mismatch | {len(bad)} |\n"
        f"| 耗时 | {stats['elapsed']:.0f}s |\n{extra}",
        encoding="utf-8")
    return rp


def mirror(src: Path = DEFAULT_ROOT, weekly: bool = True) -> int:
    """日备（方案 A）：`D:\\全量数据\\_backup\\daily\\YYYYMMDD`，7 版轮转＋周备 4 版。"""
    if not src.is_dir():
        print(f"[FAIL] 数据根不存在: {src}")
        return 1
    MIRROR_ROOT.mkdir(parents=True, exist_ok=True)
    lock = MIRROR_ROOT / ".backup.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"[FAIL] 已有备份在跑（锁 {lock}）；确认无进程后可手动删除该文件再试")
        return 1
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        stamp = datetime.now().strftime("%Y%m%d")
        daily_root = MIRROR_ROOT / "daily"
        dst = daily_root / stamp
        if dst.exists():
            print(f"[FAIL] 今日快照已存在：{dst}（日备每日一次，不覆盖）")
            return 1
        prevs = sorted([d for d in daily_root.iterdir()
                        if d.is_dir() and not d.name.startswith(".")]) if daily_root.is_dir() else []
        stage = daily_root / f".tmp-{stamp}"
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        t0 = time.time()
        print(f"[i] 日备 {src} -> {dst}（上一版：{prevs[-1].name if prevs else '无，全量'}）")
        entries, stats = _snapshot(src, stage, prevs[-1] if prevs else None)
        bad = _verify_snapshot(entries, stage)
        if bad:
            print(f"[FAIL] sha256 mismatch {len(bad)} 条（不静默，保留 {stage} 供排查）")
            for x in bad[:20]:
                print(f"  [DIFF] {x}")
            return 1
        stage.rename(dst)
        with open(dst / "sha256.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for e in entries:
                e["backup"] = str(dst / e["rel"])
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        extra = ""
        if weekly:
            wk = MIRROR_ROOT / "weekly" / datetime.now().strftime("%Y%W")
            if not wk.exists():
                wk.mkdir(parents=True)
                n = _link_tree(dst, wk)
                extra = f"| 周备 | `{wk.name}`（硬链 {n} 文件）|\n"
        stats["unique_bytes"] = _unique_bytes(MIRROR_ROOT)
        stats["ratio"] = stats["unique_bytes"] / stats["bytes"] if stats["bytes"] else 0
        report = _write_report(dst, stats, bad, extra)
        print(f"[i] 报告 {report}｜总耗时 {time.time() - t0:.0f}s｜mismatch {len(bad)}")
        removed = _rotate(daily_root, KEEP_DAILY)
        removed += _rotate(MIRROR_ROOT / "weekly", KEEP_WEEKLY)
        if removed:
            print(f"[i] 轮转滚出: {', '.join(removed)}")
        print("[OK] 备份成功，mismatch=0")
        return 0
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def package(out: Path | None = None) -> int:
    """方案 C：config／known_gaps／表清单（含 schema）／<100MB 单文件小表 → 本地 tar.xz。

    ⛔ 不上传；外发动作须老大另行点头。
    """
    import pyarrow.parquet as pq

    out = out or (MIRROR_ROOT / "pkg")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    inv = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "root": str(DEFAULT_ROOT), "tables": {}}
    groups: dict[str, list[Path]] = {}
    for f in sorted(DEFAULT_ROOT.rglob("*.parquet")):
        rel = f.relative_to(DEFAULT_ROOT)
        if rel.parts[0].startswith("_"):
            continue
        groups.setdefault(rel.parent.as_posix(), []).append(f)
    small: list[Path] = []
    for name, files in sorted(groups.items()):
        try:
            schema = {f.name: str(f.type) for f in pq.read_schema(files[0])}
        except Exception as e:  # 单表 schema 读失败不阻断整体打包
            schema = {"_error": str(e)}
        inv["tables"][name] = {
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files),
            "schema": schema,
        }
        if len(files) == 1 and files[0].stat().st_size < 100 * 1024**2 and "_meta" not in name:
            small.append(files[0])
    inv_path = out / f"table-inventory-{stamp}.json"
    inv_path.write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
    tar = out / f"config-schema-{stamp}.tar.xz"

    def _keep(ti):
        return None if "__pycache__" in Path(ti.name).parts else ti

    proj = Path(config.PROJECT_DIR)
    with tarfile.open(tar, "w:xz") as tf:
        tf.add(proj / "代码" / "config", arcname="config", filter=_keep)
        tf.add(proj / "代码" / "tools" / "known_gaps.json", arcname="known_gaps.json")
        tf.add(inv_path, arcname=inv_path.name)
        for f in small:
            tf.add(f, arcname="small/" + f.relative_to(DEFAULT_ROOT).as_posix())
    print(f"[OK] 私有仓包（本地落盘，未上传）: {tar}（{tar.stat().st_size:,} B）")
    print(f"[OK] 表清单: {inv_path}（{len(inv['tables'])} 张）｜小表入包 {len(small)} 个")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="全量数据冷备清单与校验（DB3＋R10-b）")
    parser.add_argument("--make-manifest", action="store_true", help="生成清单")
    parser.add_argument("--mirror", action="store_true",
                        help="日备（硬链增量→sha256.jsonl→7 版轮转＋周备 4 版）")
    parser.add_argument("--package", action="store_true", help="方案 C：配置/schema 打包 tar.xz（本地上传前产物）")
    parser.add_argument("--verify", metavar="MANIFEST", help="按清单校验")
    parser.add_argument("--root", default=None, help=f"数据根（默认 {DEFAULT_ROOT}；verify 时可指向镜像目录）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="清单输出目录")
    parser.add_argument("--reason", default="手动清单（DB3 轻量版）", help="清单依据")
    args = parser.parse_args(argv)

    if args.make_manifest:
        root = Path(args.root) if args.root else DEFAULT_ROOT
        return make_manifest(root, Path(args.out), args.reason)
    if args.mirror:
        return mirror(Path(args.root) if args.root else DEFAULT_ROOT)
    if args.package:
        return package()
    if args.verify:
        return verify(Path(args.verify), Path(args.root) if args.root else None)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
