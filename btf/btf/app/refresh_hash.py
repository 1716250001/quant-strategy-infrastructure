# -*- coding: utf-8 -*-
"""`bt dataset refresh-hash` —— 实算数据指纹**写回**配置（19 号 §192 X 单 / §181 D-5）。

**为什么需要**：`data.dataset.expected_hash` 由人工维护，而日更（每日 17:30）会
改变 `market_data` ⇒ 133 个声明配置当天全红，每轮人工重算 ~20 min（§190 C）。
本命令把「实算 → 写回 YAML」做成一条命令，取代人工。

**硬性约束（派单 §1.2）**：
- **X-1 行级替换**：只改 `expected_hash` 所在行里的 `sha256:` 令牌，**绝不**
  `yaml.dump` —— 配置带注释与固定键序，整体重写会丢注释/打乱顺序；
- **X-2 档位随配置**：指纹档位取该配置自己的 `data.fingerprint_mode`（缺省
  `meta`），`--mode` 仅作覆盖——写回的哈希必须与 RC-14 门禁实算时同档；
- **X-3 未声明不新增**：无 `expected_hash` 的配置（6 个 WF 专用）只报告跳过，
  **不新增键**；
- **X-4 幂等**：值已最新 ⇒ 报「已是最新」，文件字节不变；
- **X-5 写后自检**：再实算一次与写入值比对，不等即回滚该文件并记失败；
- **X-6 逐文件备份**：`<name>.bak-<YYYYMMDD-HHMMSS>`（或 `--backup-dir`）；
- **X-7 `--dry-run`**：列出 文件/旧值/新值/是否跳过，不写盘；
- **X-8 批量单失败不中断**：最后汇总「成功 / 跳过 / 失败」。

**与 `freeze` 的差异**：`freeze` 产出独立清单文件；本命令改配置本身。
两者共用 `BTFRuntime.dataset_fingerprint()`（R2 单一真值源），不另起哈希。
"""
from __future__ import annotations

import re
import shutil
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

#: 哈希令牌（与 `version.digest` 形制一致：`sha256:` + 16 位 hex）
_HASH_RE = re.compile(r"sha256:[0-9a-f]{8,64}")
#: 空声明兜底（`expected_hash: ""` / `expected_hash:`）
_EMPTY_DECL_RE = re.compile(
    r"(?P<head>\bexpected_hash\s*:\s*)(?P<val>\"\"|''|(?=[\s,}]))")


def _hash_line_index(lines: list[str]) -> int | None:
    """定位 `expected_hash` 所在行（跳过注释行）。"""
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#"):
            continue
        if "expected_hash" in ln:
            return i
    return None


def _compute_hash(path: Path, mode: str | None,
                  environ: Mapping[str, str] | None) -> tuple[str, str]:
    """实算该配置的数据指纹（档位＝配置自身 `data.fingerprint_mode`）。

    **MM-7 同源（§138.2 / freeze_dataset）**：进程内先摘掉 `expected_hash` /
    `version`，否则 RC-14 门禁会在 `build()` 里先跑，实算值与旧声明不符即
    `ConfigError`——那正是本命令要修的东西。
    """
    from btf.app import filter_btf_env
    from btf.runtime import BTFRuntime

    ovr: dict[str, Any] | None = (
        {"data": {"fingerprint_mode": mode}} if mode else None)
    rt = BTFRuntime().load_config(str(path),
                                  environ=filter_btf_env(environ),
                                  overrides=ovr)
    ds = rt.config.setdefault("data", {}).setdefault("dataset", {})
    ds.pop("expected_hash", None)
    ds.pop("version", None)
    rt.build()
    info = rt.dataset_fingerprint()
    if str(info.get("mode")) == "in-memory":
        raise ValueError("内存 Feed 无文件指纹（mode=in-memory）——"
                         "refresh-hash 只对文件主库有意义")
    return str(info.get("content_hash") or ""), str(info.get("mode") or "?")


def _backup_path(path: Path, backup_dir: Path | None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{path.name}.bak-{stamp}"
    return (backup_dir / name) if backup_dir else (path.parent / name)


def refresh_hash_one(
    path: str | Path,
    *,
    dry_run: bool = False,
    mode: str | None = None,
    backup_dir: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """单配置刷新。返回 `{file, status, old, new, mode, message}`。

    `status` ∈ `updated | current | skipped | dry-run | failed`。
    """
    p = Path(path)
    row: dict[str, Any] = {"file": str(p), "status": "", "old": "",
                           "new": "", "mode": "", "message": ""}
    try:
        text = p.read_bytes().decode("utf-8")
    except FileNotFoundError:
        row.update(status="failed", message="文件不存在")
        return row
    lines = text.splitlines(keepends=True)
    ln_no = _hash_line_index(lines)
    if ln_no is None:
        row.update(status="skipped", message="跳过（未声明 expected_hash）")
        return row
    line = lines[ln_no]
    m = _HASH_RE.search(line)
    old = m.group(0) if m else ""

    try:
        new, used_mode = _compute_hash(p, mode, environ)
    except Exception as exc:                  # X-8：单文件失败不中断批量
        row.update(status="failed",
                   message=f"实算失败：{type(exc).__name__}: {exc}")
        return row
    row.update(old=old, new=new, mode=used_mode)
    if old == new:
        row.update(status="current", message="已是最新（0 改动）")
        return row
    if dry_run:
        row.update(status="dry-run", message="将写回（dry-run 未写盘）")
        return row

    # 行级替换：只动该行的哈希令牌；空声明则补值（仍在该行内）
    if m:
        new_line = _HASH_RE.sub(new, line, count=1)
    else:
        mm = _EMPTY_DECL_RE.search(line)
        if not mm:
            row.update(status="failed",
                       message="expected_hash 行无法解析（既无 sha256 令牌亦非空声明）")
            return row
        head = mm.group("head")
        quoted = f'"{new}"'
        # 流式映射 `{expected_hash: , rules: ...}` 与块式行尾都适配
        new_line = line[:mm.start()] + f"{head}{quoted}" + line[mm.end():]
    lines[ln_no] = new_line

    bak = _backup_path(p, Path(backup_dir) if backup_dir else None)
    shutil.copy2(p, bak)
    p.write_bytes("".join(lines).encode("utf-8"))

    # X-5 写后自检：重算 → 比对；不等/异常则回滚
    try:
        check, _ = _compute_hash(p, mode, environ)
    except Exception as exc:                  # 任何失败都要回滚
        shutil.copy2(bak, p)
        row.update(status="failed",
                   message=f"写后自检异常已回滚：{type(exc).__name__}: {exc}")
        return row
    if check != new:
        shutil.copy2(bak, p)
        row.update(status="failed",
                   message=f"写后自检不等已回滚（写 {new} / 复算 {check}）")
        return row
    row.update(status="updated",
               message=f"已写回（备份 {bak.name}；档位 {used_mode}）")
    return row


def refresh_dataset_hash(
    config: str | Path | None = None,
    *,
    directory: str | Path | None = None,
    dry_run: bool = False,
    mode: str | None = None,
    backup_dir: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """`bt dataset refresh-hash` 编排：单配置或批量目录。

    批量（X-8）：单文件失败不中断；汇总 `updated / current / skipped /
    dry-run / failed`。
    """
    if bool(config) == bool(directory):
        raise ValueError("必须且只能给 --config 或 --dir 之一")
    rows: list[dict[str, Any]] = []
    if config:
        rows.append(refresh_hash_one(config, dry_run=dry_run, mode=mode,
                                     backup_dir=backup_dir, environ=environ))
    else:
        cfg_dir = Path(directory)
        if not cfg_dir.is_dir():
            raise ValueError(f"--dir 不存在或不是目录：{cfg_dir}")
        for p in sorted(cfg_dir.glob("*.yaml")):
            rows.append(refresh_hash_one(p, dry_run=dry_run, mode=mode,
                                         backup_dir=backup_dir,
                                         environ=environ))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"rows": rows, "counts": counts,
            "failed": [r for r in rows if r["status"] == "failed"]}
