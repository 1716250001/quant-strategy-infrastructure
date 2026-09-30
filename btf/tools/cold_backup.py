# -*- coding: utf-8 -*-
"""跨版本源码冷备（裁决五-1 / 裁决六-2；19 号 §37.4.2 / §38.3⑥ / §39.3）。

**为什么需要**：`btf` **不是 git 仓库**（托管明确推迟至 v1.0 私有库），
0.X 阶段"改动能否回退"此前只靠"分批提交 + 门禁 + digest 确定性"兜底；
裁决六-2 定 **冷备时机 = "修改完成、判定为真正 v1.0"时**（v1.0 定版快照）。
本工具把该动作**工具化**——定版日一条命令，产出可独立校验的冷备包。

产物（默认 `D:\\量化策略\\备份\\<YYYYMMDD-HHMM>-btf-<version>-cold\\`）：

    btf-src/            源码树副本（含 tests/tools/schemas/examples/配置）
    MANIFEST.json       版本 / 时间 / 依据 / 逐文件 sha256 / 计数 / 总字节
    SHA256SUMS.txt      纯文本校验清单（外部 `sha256sum -c` 可用）
    COLD_BACKUP.md      人类可读说明（含还原步骤）

**纪律**：
    · 只读源仓库（不写、不删、不改）；
    · 排除派生目录（`__pycache__` / `.pytest_cache` / `*.pyc` / 产物目录）；
    · **fail-closed**：任一文件哈希失败 / 源缺失 → 退出非零（不产出"半份备份"）；
    · 可核：`--verify <目录>` 复算全部哈希，与 MANIFEST 比对（防备份介质损坏）。

用法：
    python tools/cold_backup.py                    # 冷备当前版本
    python tools/cold_backup.py --reason "v1.0 定版" --out D:\\backup
    python tools/cold_backup.py --verify <目录>     # 校验既有冷备
退出码：0=成功 / 1=失败
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path(r"D:\量化策略\备份")

#: 纳入冷备的顶层条目（源码 + 配置 + 文档 + 测试 + 工具）
INCLUDE = ("btf", "tests", "tools", "schemas", "examples",
           "pyproject.toml", "CHANGELOG.md", "README.md", "docs")
#: 排除规则（派生/缓存/产物）
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                ".plugin_baseline_history.jsonl.d", "runs", "dist", "build"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log"}


def _iter_files(base: Path) -> list[Path]:
    out: list[Path] = []
    for item in sorted(base.rglob("*")):
        if item.is_dir():
            continue
        if any(part in EXCLUDE_DIRS for part in item.parts):
            continue
        if item.suffix in EXCLUDE_SUFFIX:
            continue
        out.append(item)
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version() -> str:
    for line in (ROOT / "btf" / "_version.py").read_text(
            encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip("\"'")
    raise SystemExit("[error] 未找到 __version__（btf/_version.py）")


def backup(out_root: Path, reason: str) -> Path:
    """执行冷备 → 返回冷备目录（fail-closed：任一步失败即抛）。"""
    version = _version()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    target = out_root / f"{stamp}-btf-{version}-cold"
    if target.exists():
        raise SystemExit(f"[error] 目标已存在（避免覆盖）：{target}")
    src_root = target / "btf-src"
    src_root.mkdir(parents=True)

    entries: list[dict] = []
    for name in INCLUDE:
        src = ROOT / name
        if not src.exists():
            continue
        dst = src_root / name
        if src.is_dir():
            for path in _iter_files(src):
                rel = path.relative_to(ROOT)
                out_path = src_root / rel
                out_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, out_path)
                entries.append({"file": rel.as_posix(), "sha256": _sha256(path),
                                "bytes": path.stat().st_size})
        else:
            shutil.copy2(src, dst)
            entries.append({"file": name, "sha256": _sha256(src),
                            "bytes": src.stat().st_size})

    if not entries:
        raise SystemExit("[error] 未收集到任何文件（INCLUDE 配置或源缺失）")

    manifest = {
        "version": version,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "source": str(ROOT),
        "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "files": entries,
    }
    (target / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "SHA256SUMS.txt").write_text(
        "".join(f"{e['sha256']}  btf-src/{e['file']}\n" for e in entries),
        encoding="utf-8")
    (target / "COLD_BACKUP.md").write_text(
        f"""# btf 冷备（{version}）

- 生成时间：{manifest['created_at']}
- 依据：{reason}
- 源：`{ROOT}`
- 规模：**{manifest['file_count']} 文件 / {manifest['total_bytes'] / 1024:.0f} KB**

## 还原步骤

```bash
# ① 校验完整性（必须全部 OK 才可信任）
python tools/cold_backup.py --verify "{target}"
# ② 还原（示例：还原到临时目录比对）
xcopy /E /I "btf-src" "<目标目录>"
```

## 校验清单

- `MANIFEST.json`：逐文件 sha256 + 计数（机器可读）
- `SHA256SUMS.txt`：`sha256sum -c SHA256SUMS.txt` 可用（工作目录 = 本目录）
""", encoding="utf-8")
    return target


def verify(target: Path) -> list[str]:
    """校验既有冷备 → 问题清单（空 = 一致）。"""
    problems: list[str] = []
    manifest_path = target / "MANIFEST.json"
    if not manifest_path.is_file():
        return [f"缺 MANIFEST.json：{target}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = target / "btf-src" / entry["file"]
        if not path.is_file():
            problems.append(f"缺失：{entry['file']}")
            continue
        got = _sha256(path)
        if got != entry["sha256"]:
            problems.append(f"哈希不符：{entry['file']}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="btf 跨版本源码冷备")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="备份根目录")
    parser.add_argument("--reason", default="手动冷备",
                        help="依据（写入 MANIFEST；v1.0 定版请写明）")
    parser.add_argument("--verify", metavar="DIR", help="校验既有冷备目录")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.verify:
        problems = verify(Path(args.verify))
        if problems:
            print(f"[fail] 冷备校验不一致（{len(problems)} 处）：")
            for p in problems[:20]:
                print(f"  - {p}")
            return 1
        print(f"[ok] 冷备校验通过：{args.verify}（逐文件 sha256 一致）")
        return 0

    target = backup(Path(args.out), args.reason)
    manifest = json.loads((target / "MANIFEST.json").read_text(encoding="utf-8"))
    print(f"[ok] 冷备完成：{target}")
    print(f"     版本 {manifest['version']} | {manifest['file_count']} 文件 | "
          f"{manifest['total_bytes'] / 1024:.0f} KB | 依据={manifest['reason']}")
    problems = verify(target)
    if problems:
        print(f"[fail] 冷备自校验失败（{len(problems)} 处）")
        return 1
    print("[ok] 自校验通过（逐文件 sha256 一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
