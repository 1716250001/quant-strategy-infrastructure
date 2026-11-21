# -*- coding: utf-8 -*-
"""REQ-20261121-01：`tools/check_v77_digest.py` 默认入口 smoke（进 CI）。

校验对象已于 REQ-20261121-01 改为赤潮 v7.8 单一规则源 `AGENTS.md` 整文件 digest。
沿用 R42′/N45 纪律：① 走**默认入口**；② 取**独立退出码**（subprocess returncode，
不经管道——管道会吞退出码，历史上造成「崩溃被判通过」）。

单函数 9 子例（不新增测试函数，collect 数不变）：
  1 默认入口正例（rc=0 / [ok] / --out JSON ok=true）
  2 单字符负向（rc=1 / [mismatch]）
  3 CRLF 等价（rc=0 / [ok]）
  4 源缺失（rc=2 / stderr [error] AGENTS.md）
  5 `--update` 缺 reason（rc=2 / 基线不落盘）
  6 重冻结留痕（rc=0×2 / previous 长度 1 且旧 sha256 留痕）
  7 `--update` 与 `--out` 互斥（rc=2 / 两者均不落盘）
  8 基线 schema 严格校验（缺 size_bytes / 非法 sha256 → rc=2）
  9 原子写无残留（`*.tmp` 为空且正式基线可解析）
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]      # btf/
TOOL = ROOT / "tools" / "check_v77_digest.py"


def _run(args, env_extra=None):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("BTFTOOL_AGENTS_MD", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(TOOL), *args], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120, env=env)


@pytest.mark.smoke
def test_default_entry_smoke_independent_exit_code(tmp_path):
    # 子例 1：默认入口正例
    out = tmp_path / "out.json"
    p = _run(["--out", str(out)])
    assert p.returncode == 0, (
        f"子例 1：默认入口失败（rc={p.returncode}）：\n"
        f"{p.stdout[-1500:]}\n{p.stderr[-800:]}")
    assert "[ok]" in p.stdout, f"子例 1：stdout 缺 [ok]：\n{p.stdout[-1500:]}"
    assert "digest = sha256:" in p.stdout, (
        f"子例 1：stdout 缺 digest 行：\n{p.stdout[-1500:]}")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data.get("schema") == "agents-digest-check/v1", "子例 1：--out schema 非法"
    assert data.get("ok") is True, f"子例 1：--out ok={data.get('ok')}"
    assert data.get("digest") and data["digest"] in p.stdout, (
        "子例 1：--out digest 与 stdout 不一致")
    source = pathlib.Path(data.get("source", ""))
    assert source.is_file(), f"子例 1：--out source 不可读：{source}"
    raw = source.read_bytes()

    # 子例 2：单字符负向（首个 ASCII 字母 → 另一字母）
    idx = next((i for i, b in enumerate(raw) if 65 <= b <= 90 or 97 <= b <= 122),
               None)
    assert idx is not None, "子例 2：源文件无 ASCII 字母，无法构造单字符改动"
    repl = 0x41 if raw[idx] != 0x41 else 0x42
    mutated = tmp_path / "agents_mutated.md"
    mutated.write_bytes(raw[:idx] + bytes([repl]) + raw[idx + 1:])
    p = _run([], {"BTFTOOL_AGENTS_MD": str(mutated)})
    assert p.returncode == 1, (
        f"子例 2：单字符改动未触发失败（rc={p.returncode}）：\n"
        f"{p.stdout[-1500:]}")
    assert "[mismatch]" in p.stdout, (
        f"子例 2：stdout 缺 [mismatch]：\n{p.stdout[-1500:]}")

    # 子例 3：CRLF 等价（归一化后 digest 不变）
    crlf = tmp_path / "agents_crlf.md"
    crlf.write_bytes(raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    p = _run([], {"BTFTOOL_AGENTS_MD": str(crlf)})
    assert p.returncode == 0, (
        f"子例 3：CRLF 等价副本应通过（rc={p.returncode}）：\n"
        f"{p.stdout[-1500:]}")
    assert "[ok]" in p.stdout, f"子例 3：stdout 缺 [ok]：\n{p.stdout[-1500:]}"

    # 子例 4：源缺失
    p = _run([], {"BTFTOOL_AGENTS_MD": str(tmp_path / "not-exist.md")})
    assert p.returncode == 2, f"子例 4：源缺失应 rc=2（实际 {p.returncode}）"
    assert "[error]" in p.stderr and "AGENTS.md" in p.stderr, (
        f"子例 4：stderr 缺 [error] AGENTS.md：\n{p.stderr[-800:]}")

    # 子例 5：--update 缺 reason（不得落盘基线）
    baseline = tmp_path / "baseline.json"
    p = _run(["--update", "--baseline", str(baseline)],
             {"BTFTOOL_AGENTS_MD": str(mutated)})
    assert p.returncode == 2, (
        f"子例 5：--update 缺 reason 应 rc=2（实际 {p.returncode}）")
    assert "[error]" in p.stderr, f"子例 5：stderr 缺 [error]：\n{p.stderr[-800:]}"
    assert not baseline.exists(), "子例 5：缺 reason 时不得写基线"

    # 子例 6：重冻结留痕（旧值进 previous）
    copy = tmp_path / "agents_copy.md"
    copy.write_bytes(raw)
    p1 = _run(["--update", "--reason", "第一次", "--baseline", str(baseline)],
              {"BTFTOOL_AGENTS_MD": str(copy)})
    assert p1.returncode == 0, (
        f"子例 6：首次重冻结失败（rc={p1.returncode}）：\n{p1.stderr[-800:]}")
    first = json.loads(baseline.read_text(encoding="utf-8"))
    third = 0x5A if raw[idx] != 0x5A else 0x59
    copy.write_bytes(raw[:idx] + bytes([third]) + raw[idx + 1:])
    p2 = _run(["--update", "--reason", "第二次", "--baseline", str(baseline)],
              {"BTFTOOL_AGENTS_MD": str(copy)})
    assert p2.returncode == 0, (
        f"子例 6：二次重冻结失败（rc={p2.returncode}）：\n{p2.stderr[-800:]}")
    second = json.loads(baseline.read_text(encoding="utf-8"))
    assert second["sha256"] != first["sha256"], "子例 6：改动后 digest 未变化"
    assert second["reason"] == "第二次", "子例 6：reason 未更新"
    assert len(second["previous"]) == 1, (
        f"子例 6：previous 长度={len(second['previous'])}")
    assert second["previous"][0]["sha256"] == first["sha256"], (
        "子例 6：旧 sha256 未留痕")

    # 子例 7：--update 与 --out 互斥（均不落盘）
    out2 = tmp_path / "out2.json"
    base2 = tmp_path / "base2.json"
    p = _run(["--update", "--out", str(out2), "--reason", "x",
              "--baseline", str(base2)],
             {"BTFTOOL_AGENTS_MD": str(copy)})
    assert p.returncode == 2, (
        f"子例 7：--update+--out 应 rc=2（实际 {p.returncode}）")
    assert "[error]" in p.stderr and "--out" in p.stderr, (
        f"子例 7：stderr 缺 [error]/--out：\n{p.stderr[-800:]}")
    assert not base2.exists(), "子例 7：互斥时不得写基线"
    assert not out2.exists(), "子例 7：互斥时不得写 --out"

    # 子例 8：基线 schema 严格校验（缺字段 / 非法 sha256 均 rc=2）
    base3 = tmp_path / "base3.json"
    p = _run(["--update", "--reason", "严格校验", "--baseline", str(base3)],
             {"BTFTOOL_AGENTS_MD": str(copy)})
    assert p.returncode == 0, (
        f"子例 8：准备基线失败（rc={p.returncode}）：\n{p.stderr[-800:]}")
    strict = json.loads(base3.read_text(encoding="utf-8"))
    del strict["size_bytes"]
    base3.write_text(json.dumps(strict, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    p = _run(["--baseline", str(base3)], {"BTFTOOL_AGENTS_MD": str(copy)})
    assert p.returncode == 2, (
        f"子例 8a：缺 size_bytes 应 rc=2（实际 {p.returncode}）")
    assert "[error]" in p.stderr and "schema 非法" in p.stderr, (
        f"子例 8a：stderr 缺 schema 非法：\n{p.stderr[-800:]}")
    strict["size_bytes"] = 1
    strict["sha256"] = "sha256:xyz"
    base3.write_text(json.dumps(strict, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    p = _run(["--baseline", str(base3)], {"BTFTOOL_AGENTS_MD": str(copy)})
    assert p.returncode == 2, (
        f"子例 8b：非法 sha256 应 rc=2（实际 {p.returncode}）")
    assert "[error]" in p.stderr and "schema 非法" in p.stderr, (
        f"子例 8b：stderr 缺 schema 非法：\n{p.stderr[-800:]}")

    # 子例 9：原子写无残留（子例 6 两次 --update 后）
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"子例 9：残留临时文件：{leftovers}"
    json.loads(baseline.read_text(encoding="utf-8"))
