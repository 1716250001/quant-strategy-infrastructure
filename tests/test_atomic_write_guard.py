# -*- coding: utf-8 -*-
"""落盘原子化守卫（派单 B 返工版，2026-11-12）
================================================

为什么：`df.to_parquet(path)` 直接覆盖目标文件，写中途崩溃/断电会同时失去
新旧数据。全库唯一的原子写入口是 `common/parquet_store._atomic_write_parquet`
（tmp → fsync → os.replace → 目录 fsync）。改完存量 12 处只解决今天，
下一个人再写一处裸 `to_parquet` 没人发现 —— 本守卫负责当天就红。

判定（三选一，逐调用点）：
  ① 位于 `_atomic_write_parquet` 函数体内（封装本体）；
  ② 同一函数内、写入行前后 3 行内出现 `os.replace(`；
  ③ 文件在 ALLOWLIST 中（逐条给理由）。

扫描面：全库 `.py`，排除 `_venv`/`_scratch`/`_archive`/`tests`（派单 §3.2），
另排除 `.git`/`__pycache__`/`.pytest_cache`（非源码）与 `btf`（独立子仓，
19 号 §263 规定不碰）。

纪律：本文件只断言，不改生产代码；红了直接指向「文件:行 + 原始语句」。
"""
import ast
import pathlib

CODE_ROOT = pathlib.Path(__file__).resolve().parents[1]

# 派单 §3.2 指定排除 + 非源码目录 + 独立子仓
SKIP_PARTS = {
    "_venv", "_scratch", "_archive", "tests",     # 派单原文
    ".git", "__pycache__", ".pytest_cache",       # 非源码
    "btf",                                        # 独立子仓（19 号 §263）
}

# 白名单：仅这三个文件允许出现裸 `.to_parquet(`，逐条理由见下。
ALLOWLIST = {
    "common/parquet_store.py":
        "原子写封装本体（_atomic_write_parquet 自身必须以 to_parquet 落 tmp）",
    "tools/db/clean.py":
        "已是原子写：tmp → to_parquet → 回读校验 len(chk)==n1 → os.replace",
    "alt/us_backfill.py":
        "已是原子写：tmp → to_parquet → os.replace",
}

WINDOW = 3          # 同函数内、写入行前后 3 行内有 os.replace 即视为原子


def _iter_py_files():
    """产出 (绝对路径, 相对 CODE_ROOT 的 posix 路径)，按派单排除面过滤。"""
    for p in sorted(CODE_ROOT.rglob("*.py")):
        rel = p.relative_to(CODE_ROOT)
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        yield p, rel.as_posix()


def _function_spans(text):
    """[(起始行, 结束行, 函数名)]，1-based 闭区间；语法错误时返回空表。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((node.lineno, node.end_lineno or node.lineno, node.name))
    return spans


def _enclosing(spans, lineno):
    """最内层包含 lineno 的函数；不在任何函数内返回 None。"""
    best = None
    for start, end, name in spans:
        if start <= lineno <= end:
            if best is None or (end - start) < (best[1] - best[0]):
                best = (start, end, name)
    return best


def _write_points():
    """全库所有 `.to_parquet(` 调用点：(相对路径, 行号, 语句, 函数跨度, 全文行)。"""
    points = []
    for p, rel in _iter_py_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        spans = _function_spans(text)
        for lineno, line in enumerate(lines, 1):
            if ".to_parquet(" in line:
                points.append((rel, lineno, line.strip(), spans, lines))
    return points


def _is_atomic(rel, lineno, spans, lines):
    if rel in ALLOWLIST:                                  # ③ 白名单
        return True
    fn = _enclosing(spans, lineno)
    if fn is None:
        return False
    start, end, name = fn
    if rel == "common/parquet_store.py" and name == "_atomic_write_parquet":
        return True                                       # ① 封装本体
    lo, hi = max(start, lineno - WINDOW), min(end, lineno + WINDOW)
    return any("os.replace(" in lines[j - 1] for j in range(lo, hi + 1))  # ② 近邻 replace


def test_no_direct_to_parquet():
    """全库不得存在绕过 _atomic_write_parquet 的裸写。"""
    violations = [
        (rel, lineno, line)
        for rel, lineno, line, spans, lines in _write_points()
        if not _is_atomic(rel, lineno, spans, lines)
    ]
    detail = "\n".join(f"  {rel}:{lineno}  {line}" for rel, lineno, line in violations)
    assert not violations, (
        f"发现 {len(violations)} 处绕过 common.parquet_store._atomic_write_parquet 的裸写：\n"
        f"{detail}\n"
        "修复：改为 `_atomic_write_parquet(df, path)`；"
        "若确有理由保留裸写，必须先经赤潮裁定再加入 ALLOWLIST 并写清理由。"
    )


def test_allowlist_not_rot():
    """反向断言：白名单文件必须仍含 `.to_parquet(`。

    防的不是错，是僵化：若某天 clean.py 改为调用封装、白名单却没人删，
    这条会红，提醒维护者把它移出白名单（否则白名单会腐化成空壳）。
    """
    for rel, reason in ALLOWLIST.items():
        p = CODE_ROOT / rel
        assert p.exists(), f"白名单文件不存在：{rel}（理由：{reason}）"
        text = p.read_text(encoding="utf-8", errors="replace")
        assert ".to_parquet(" in text, (
            f"白名单已腐化：{rel} 不再包含 `.to_parquet(`（理由曾为：{reason}）。"
            "请把它从 ALLOWLIST 删除。"
        )
