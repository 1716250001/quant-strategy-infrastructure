# -*- coding: utf-8 -*-
"""冒烟测试（阶段 0 任务 0.4）：中文路径 / 环境指纹 / 路径派生纪律。

对应架构锚点：
    - 06 §11.3 部署要点 3：中文路径全链路 UTF-8
    - ADR-4：路径经环境变量派生，禁止硬编码盘符
    - 08 §13.2（E2）：环境指纹运行时实采，不抄录文档
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import btf
import pytest
from btf.config import paths


@pytest.mark.smoke
class TestPackageSkeleton:
    """包骨架完整性（任务 0.1）。"""

    def test_import_and_version(self):
        """版本号**形制**断言（`X.Y.Z` 三段数字）。

        `1.0.0` 定版（FF-3）：原为写死前缀 `startswith("0.5")` ⇒ 每次大版本
        bump 都会假红（赤潮轮次十二全仓扫描发现两处同族写法）。改为形制断言，
        **不再写死任何版本前缀**（v2.0 亦不会复发）。
        """
        assert re.fullmatch(r"\d+\.\d+\.\d+", btf.__version__), btf.__version__

    def test_all_modules_importable(self):
        """13 个架构模块全部可导入（03 §7.2 分层表）。"""
        for mod in (
            "btf.domain", "btf.data", "btf.engine", "btf.execution",
            "btf.risk", "btf.portfolio", "btf.strategy", "btf.analytics",
            "btf.experiment", "btf.config", "btf.registry", "btf.viz", "btf.cli",
        ):
            __import__(mod)


@pytest.mark.smoke
class TestChinesePathUtf8:
    """中文路径全链路（06 §11.3 部署要点 3）。"""

    def test_project_root_contains_chinese(self):
        """本项目就在中文路径上——这是最真实的冒烟环境。"""
        assert any(ord(c) > 127 for c in str(paths.PACKAGE_ROOT))

    def test_utf8_read_write_roundtrip(self, tmp_path: Path):
        """中文路径下 UTF-8 读写往返（显式 encoding 纪律）。"""
        target = tmp_path / "中文目录" / "文件_回测.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = "资金曲线：Δ现金+Δ市值(名义)+费用=0 ✔"
        target.write_text(payload, encoding="utf-8")
        assert target.read_text(encoding="utf-8") == payload

    def test_default_output_dir_chinese_ok(self):
        """默认产物目录名含中文（回测产物/）——仅验证路径对象可用，不实际创建。"""
        assert "回测产物" in str(paths.OUTPUT_DIR) or os.environ.get("BTF_OUTPUT_DIR")


#: D-4（19 号 §159）／L-3（§161）：硬编码盘符守卫扫描面——
#: btf 包 ＋ examples ＋ tools ＋ tests（**排除守卫自身**）。
_BTF_ROOT = Path(__file__).resolve().parents[1]
#: 守卫自身豁免（§161 L-3-2）：token 元组与反向注入样本**必须**含盘符字面量。
_GUARD_SELF = {_BTF_ROOT / "tests" / "test_smoke.py"}
#: 文档性 docstring 开引号：认 `r"""`／`f"""`／`rb'''` 等前缀（§161 D-3-1）。
_DOC_OPEN = re.compile(r"^[rRbBuUfF]{0,2}(\"\"\"|''')")
#: 盘符 token：反斜杠四式 ＋ 正斜杠四式（§161 L-4；宁窄不宽，不泛化全盘符）。
_DRIVE_TOKENS = ("D:\\", "C:\\", "d:\\", "c:\\",
                 "D:/", "C:/", "d:/", "c:/")


def _scan_files() -> list[Path]:
    # 返回扫描文件清单（按路径排序；排除 __pycache__ 与守卫自身）。
    root = _BTF_ROOT
    files: list[Path] = []
    files.extend(p for p in (root / "btf").rglob("*.py")
                 if "__pycache__" not in p.parts)
    files.extend(p for p in (root / "examples").rglob("*")
                 if p.suffix in {".py", ".yaml"} and "__pycache__" not in p.parts)
    files.extend((root / "tools").glob("*.py"))
    files.extend(p for p in (root / "tests").rglob("*.py")
                 if "__pycache__" not in p.parts and p not in _GUARD_SELF)
    return sorted(set(files))


def _hardcoded_drive_violations(files: list[Path]) -> list[str]:
    # 扫描清单 → 违例清单（「文件:行号: 代码行」；空 = 全绿）。
    # 判据：跳过 docstring（含 r""" 等前缀）与注释（整行 #／行内 " # "），
    # 仅对可执行代码断言不含 8 种盘符 token。
    problems: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        in_doc = False
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            if path.suffix == ".py":
                m = _DOC_OPEN.match(line)
                if m:
                    quote = m.group(1)
                    rest = line[m.end(1):]
                    if rest and rest.endswith(quote):
                        continue  # 单行 docstring（含前缀）
                    in_doc = not in_doc
                    continue
                if in_doc or line.startswith("#"):
                    continue
            elif line.startswith("#"):
                continue
            code = raw.split(" # ", 1)[0]
            for token in _DRIVE_TOKENS:
                if token in code:
                    problems.append(f"{path}:{i}: {code.strip()}")
                    break
    return problems


@pytest.mark.smoke
class TestPathsDiscipline:
    """路径派生纪律（ADR-4：反例=config.py:101 硬编码盘符）。"""

    def test_env_override_wins(self, monkeypatch, tmp_path):
        target = tmp_path / "btf_test_out"
        monkeypatch.setenv("BTF_OUTPUT_DIR", str(target))
        import importlib

        from btf.config import paths as p2
        importlib.reload(p2)
        try:
            assert p2.OUTPUT_DIR == target.resolve()
        finally:
            monkeypatch.delenv("BTF_OUTPUT_DIR")
            importlib.reload(paths)

    def test_no_hardcoded_drive_in_source(self):
        """全仓源码禁止硬编码盘符（ADR-4；L-3 扩至 tests）。

        §161 L-3：扫描面 = `btf/**/*.py` ＋ `examples/**/*.{py,yaml}` ＋
        `tools/*.py` ＋ `tests/**/*.py`（**排除守卫自身** `test_smoke.py`）；
        跳过 docstring（含 `r\"\"\"` 等前缀）与注释后，对可执行代码断言不含
        8 种盘符 token（反斜杠四式 ＋ 正斜杠四式）。扫描条目数须 ≥ 235（K-0 后实测）。
        """
        files = _scan_files()
        assert len(files) >= 235, f"扫描面不足：{len(files)}（期望 ≥235）"
        problems = _hardcoded_drive_violations(files)
        print(f"\n[drive-guard] 扫描 {len(files)} 文件 ⇒ {len(problems)} 违例")
        assert not problems, "硬编码盘符违例：\n" + "\n".join(problems)


    def test_runs_dir_under_output(self):
        assert paths.RUNS_DIR.parent == paths.OUTPUT_DIR

@pytest.mark.smoke
def test_guard_catches_hardcoded_drive(tmp_path):
    """守卫必须能红：反斜杠＋正斜杠＋raw docstring 三组（§159 D-4／§161 L-3/L-4）。"""
    bad = tmp_path / "bad_drive.py"
    bad.write_text('X = r"D:\\foo"\n', encoding="utf-8")
    problems = _hardcoded_drive_violations([bad])
    print("\n[reverse] 注入 D:\\ 违例 ⇒ "
          + ("\n".join(problems) if problems else "未检出"))
    assert problems, "守卫未检出硬编码盘符（失效）"
    assert "bad_drive.py" in problems[0]

    forward = tmp_path / "bad_forward.py"
    forward.write_text('X = "D:/foo"\n', encoding="utf-8")
    f_problems = _hardcoded_drive_violations([forward])
    print("[reverse] 注入 D:/ 违例 ⇒ "
          + ("\n".join(f_problems) if f_problems else "未检出"))
    assert f_problems, "守卫未检出正斜杠盘符（失效）"

    raw_doc = tmp_path / "raw_doc.py"
    raw_doc.write_text('r"""\nX = r"D:\\foo"\n"""\n', encoding="utf-8")
    doc_problems = _hardcoded_drive_violations([raw_doc])
    print("[reverse] r\"\"\" docstring 内含 D:\\ ⇒ "
          + ("\n".join(doc_problems) if doc_problems else "未报（正确）"))
    assert not doc_problems, "守卫误报 docstring 内的盘符（raw 前缀漏判）"

    code_file = tmp_path / "raw_code.py"
    code_file.write_text('X = r"D:\\foo"\n', encoding="utf-8")
    code_problems = _hardcoded_drive_violations([code_file])
    print("[reverse] 同类路径写在代码行 ⇒ "
          + ("\n".join(code_problems) if code_problems else "未检出"))
    assert code_problems, "守卫未检出代码行盘符"

    current = _hardcoded_drive_violations(_scan_files())
    print(f"[reverse] 现状扫描 ⇒ {len(current)} 违例")
    assert not current


@pytest.mark.smoke
class TestEnvFingerprint:
    """环境指纹运行时实采（E2：manifest env_version 的数据源）。"""

    def test_python_version_runtime(self):
        v = sys.version_info
        assert (v.major, v.minor) == (3, 13), "架构基线 Python 3.13（01 §C2）"

    def test_key_libs_importable(self):
        """数据底座关键库在位（README §五 事实锚定）。"""
        import numpy  # noqa: F401
        import pyarrow  # noqa: F401
