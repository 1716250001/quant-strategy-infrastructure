# -*- coding: utf-8 -*-
"""btf run 包装器：自动对齐 RC-14 数据哈希后执行（两段式）。

用法：
    python tools/run_patched.py examples/config_x.yaml

第一段：以占位哈希运行，从错误里抓「实算 sha256:xxxx」；
第二段：回填 expected_hash 后正式运行，透传退出码与输出。
仅改 `data.dataset.expected_hash`，不动其他键。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BT = ROOT.parent / "_venv" / "Scripts" / "bt.exe"
_ERR = re.compile(r"实算\s+sha256:([0-9a-f]{8,})")


def _run(cfg: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(BT), "run", "--config", str(cfg)],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：python tools/run_patched.py <config.yaml>")
        return 2
    cfg = Path(argv[1])
    if not cfg.is_absolute():
        cfg = ROOT / cfg
    text = cfg.read_text(encoding="utf-8")

    first = _run(cfg)
    m = _ERR.search(first.stdout + first.stderr)
    if m:
        text = re.sub(r"expected_hash:\s*\"sha256:[0-9a-f]+\"",
                      f'expected_hash: "sha256:{m.group(1)}"', text, count=1)
        cfg.write_text(text, encoding="utf-8")
        second = _run(cfg)
        out = second
    else:
        out = first
    sys.stdout.write(out.stdout)
    sys.stderr.write(out.stderr)
    return out.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
