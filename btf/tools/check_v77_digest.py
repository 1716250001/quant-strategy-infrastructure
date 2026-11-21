# -*- coding: utf-8 -*-
"""赤潮规则源 `AGENTS.md` 整文件 digest 校验（REQ-20261121-01）。

校验对象已于 REQ-20261121-01 由**已退役**的 v7.7 规则镜像
`赤潮/docs/rules_mirror_v77.json` 改为赤潮 v7.8 单一规则源 `赤潮/AGENTS.md`
（规则镜像与 v77 策略资产双缺，旧三档 metrics 复跑路径不可恢复）。

digest 口径：整文件 sha256（**CRLF → LF 归一化后**；与
`tools/check_plugin_boundary.py::_sha256_file` 同法，跨机器/跨检出稳定）。
`size_bytes` / `line_count` 仅辅助披露，**不参与判定**。

基线：`tools/agents_digest_baseline.json`（schema `agents-digest-baseline/v1`）。
规则变更须显式 `--update --reason TEXT` 重冻结（reason 必填；旧
`{sha256, frozen_at, reason}` 追加进 `previous`）；门禁只比对、不自动刷新。

源路径解析（优先级）：
    1. 环境变量 `BTFTOOL_AGENTS_MD`（绝对路径覆盖，沿用 `BTFTOOL_` 前缀纪律）
    2. `tools/_paths.CHICHAO_ROOT / "AGENTS.md"`（既有单点派生）

退出码：`0`＝一致；`1`＝不一致；`2`＝源/基线不可读、schema 非法、
`--update` 缺 `reason`。

用法：
    python tools/check_v77_digest.py [--baseline PATH] [--out JSON]
    python tools/check_v77_digest.py --update --reason "规则版本变更说明"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _paths import CHICHAO_ROOT

TOOLS_DIR = Path(__file__).resolve().parent
BASELINE = TOOLS_DIR / "agents_digest_baseline.json"
BASELINE_SCHEMA = "agents-digest-baseline/v1"
CHECK_SCHEMA = "agents-digest-check/v1"
TARGET = "赤潮/AGENTS.md"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class BaselineError(Exception):
    """基线不可读或 schema 非法（映射 exit 2）。"""


def _harden_stdout() -> None:
    """让 stdout 对不可编码字符降级而非抛错（N45 纪律，2026-10-11 实证）。

    stdout 被管道/CI 捕获时 Python 用 locale 编码（GBK），不可编码字符会让
    `print` 抛 UnicodeEncodeError ⇒ 判定通过却以退出码 1 结束。errors="replace"
    仅降级符号本身，判定链（digest 行）完全保真。
    """
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass


def resolve_source() -> Path:
    """按优先级解析 AGENTS.md 路径（env 覆盖 → `_paths` 单点派生）。"""
    override = os.environ.get("BTFTOOL_AGENTS_MD")
    if override:
        return Path(override)
    return CHICHAO_ROOT / "AGENTS.md"


def measure(path: Path) -> dict:
    """实测 digest / size / lines（CRLF→LF 归一化后哈希）。"""
    raw = path.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    return {
        "digest": "sha256:" + hashlib.sha256(normalized).hexdigest(),
        "size_bytes": len(raw),
        "line_count": len(normalized.splitlines()),
    }


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_pos_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def load_baseline(path: Path) -> dict:
    """读取并严格校验基线结构；任何异常统一抛 `BaselineError`。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineError(str(path)) from exc
    if (not isinstance(data, dict)
            or data.get("schema") != BASELINE_SCHEMA
            or not isinstance(data.get("target"), str) or not data["target"]
            or not isinstance(data.get("frozen_at"), str) or not data["frozen_at"]
            or not isinstance(data.get("reason"), str) or not data["reason"]
            or not isinstance(data.get("sha256"), str)
            or not _SHA256_RE.match(data["sha256"])
            or not _is_pos_int(data.get("size_bytes"))
            or not _is_pos_int(data.get("line_count"))
            or not isinstance(data.get("previous"), list)):
        raise BaselineError(str(path))
    for entry in data["previous"]:
        if (not isinstance(entry, dict)
                or not _SHA256_RE.match(str(entry.get("sha256", "")))
                or not isinstance(entry.get("frozen_at"), str)
                or not isinstance(entry.get("reason"), str)):
            raise BaselineError(str(path))
    return data


def _write_json(path: Path, payload: dict) -> None:
    """同目录临时文件 ＋ `os.replace` 原子替换（F3；失败不破坏正式文件）。"""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    _harden_stdout()
    ap = argparse.ArgumentParser(
        description="赤潮 AGENTS.md 整文件 digest 校验（REQ-20261121-01）")
    ap.add_argument("--baseline", default=str(BASELINE),
                    help="基线 JSON 路径（默认 tools/agents_digest_baseline.json）")
    ap.add_argument("--out", default=None,
                    help="实测 JSON 输出路径（校验模式，可选）")
    ap.add_argument("--update", action="store_true",
                    help="重冻结基线（必须同时给 --reason；旧值追加进 previous）")
    ap.add_argument("--reason", default="", help="重冻结依据（--update 必填）")
    args = ap.parse_args(argv)

    if args.update and args.out:
        print("[error] --out 仅用于校验模式，不能与 --update 同用",
              file=sys.stderr)
        return 2

    if args.update and not args.reason.strip():
        print("[error] --update 必须带 --reason", file=sys.stderr)
        return 2

    source = resolve_source()
    baseline_path = Path(args.baseline)

    try:
        actual = measure(source)
    except OSError:
        print(f"[error] AGENTS.md 不存在或不可读：{source}", file=sys.stderr)
        return 2

    if args.update:
        try:
            old = load_baseline(baseline_path) if baseline_path.exists() else None
        except BaselineError:
            print(f"[error] 基线不可读或 schema 非法：{baseline_path}",
                  file=sys.stderr)
            return 2
        previous = list(old.get("previous", [])) if old else []
        if old:
            previous.append({
                "sha256": old["sha256"],
                "frozen_at": old.get("frozen_at"),
                "reason": old.get("reason"),
            })
        payload = {
            "schema": BASELINE_SCHEMA,
            "target": TARGET,
            "frozen_at": _now(),
            "reason": args.reason.strip(),
            "sha256": actual["digest"],
            "size_bytes": actual["size_bytes"],
            "line_count": actual["line_count"],
            "previous": previous,
        }
        try:
            _write_json(baseline_path, payload)
        except OSError:
            print(f"[error] 基线写入失败：{baseline_path}", file=sys.stderr)
            return 2
        old_digest = old["sha256"] if old else "(new)"
        print(f"[ok] baseline updated: {old_digest} -> {actual['digest']}",
              flush=True)
        return 0

    try:
        base = load_baseline(baseline_path)
    except BaselineError:
        print(f"[error] 基线不可读或 schema 非法：{baseline_path}",
              file=sys.stderr)
        return 2

    base_digest = base["sha256"]
    ok = actual["digest"] == base_digest
    if ok:
        print(f"[ok] source = {source}", flush=True)
        print(f"[ok] size = {actual['size_bytes']} B | lines = "
              f"{actual['line_count']}", flush=True)
        print(f"digest = {actual['digest']}", flush=True)
        print(f"baseline = {base_digest}", flush=True)
        print("[ok] AGENTS.md digest 校验通过", flush=True)
    else:
        print(f"[mismatch] source = {source}", flush=True)
        print(f"digest = {actual['digest']}", flush=True)
        print(f"baseline = {base_digest}", flush=True)
        print("[mismatch] AGENTS.md digest 不匹配（如属规则变更，请 "
              "--update --reason 重冻结）", flush=True)

    if args.out:
        try:
            _write_json(Path(args.out), {
                "schema": CHECK_SCHEMA,
                "source": str(source),
                "digest": actual["digest"],
                "size_bytes": actual["size_bytes"],
                "line_count": actual["line_count"],
                "baseline": str(baseline_path),
                "baseline_digest": base_digest,
                "ok": ok,
                "checked_at": _now(),
            })
        except OSError:
            print(f"[error] 实测 JSON 写入失败：{args.out}", file=sys.stderr)
            return 2
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
