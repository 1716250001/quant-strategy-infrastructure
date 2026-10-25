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
        assert "量化策略" in str(paths.PACKAGE_ROOT)

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


#: D-4（19 号 §159）：硬编码盘符守卫扫描面——btf 包 ＋ examples ＋ tools；
#: **不扫 tests/**（跨仓对账测试需真实私有路径，收敛属另案）。
def _scan_files() -> list[Path]:
    # 返回扫描文件清单（按路径排序；排除 __pycache__）。
    root = Path(__file__).resolve().parents[1]
    files: list[Path] = []
    files.extend(p for p in (root / "btf").rglob("*.py")
                 if "__pycache__" not in p.parts)
    files.extend(p for p in (root / "examples").rglob("*")
                 if p.suffix in {".py", ".yaml"} and "__pycache__" not in p.parts)
    files.extend((root / "tools").glob("*.py"))
    return sorted(set(files))


def _hardcoded_drive_violations(files: list[Path]) -> list[str]:
    # 扫描清单 → 违例清单（「文件:行号: 代码行」；空 = 全绿）。
    # 判据：跳过 docstring（状态机）与注释（整行 #／行内 " # "），
    # 仅对可执行代码断言不含 D:\\／C:\\ 字面量。
    problems: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        in_doc = False
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            if path.suffix == ".py":
                if line.startswith((chr(34) * 3, chr(39) * 3)):
                    quote = line[:3]
                    if line.endswith(quote) and len(line) > 3:
                        continue
                    in_doc = not in_doc
                    continue
                if in_doc or line.startswith("#"):
                    continue
            elif line.startswith("#"):
                continue
            code = raw.split(" # ", 1)[0]
            for token in ("D:\\", "C:\\", "d:\\", "c:\\"):
                if token in code:
                    problems.append(f"{path}:{i}: {code.strip()}")
                    break
    return problems


@pytest.mark.smoke
class TestPathsDiscipline:
    """路径派生纪律（ADR-4：反例=config.py:101 硬编码盘符）。"""

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("BTF_OUTPUT_DIR", r"D:\tmp\btf_test_out")
        import importlib

        from btf.config import paths as p2
        importlib.reload(p2)
        try:
            assert p2.OUTPUT_DIR == Path(r"D:\tmp\btf_test_out").resolve()
        finally:
            monkeypatch.delenv("BTF_OUTPUT_DIR")
            importlib.reload(paths)

    def test_no_hardcoded_drive_in_source(self):
        """全仓源码禁止硬编码盘符（ADR-4；D-4 扩至 btf/examples/tools）。

        §159 D-4：扫描面 = `btf/**/*.py` ＋ `examples/**/*.{py,yaml}` ＋
        `tools/*.py`（**不扫 tests/**）；跳过 docstring 与注释后，对可执行
        代码断言不含 `D:\\`／`C:\\` 字面量。扫描条目数须 ≥ 105。
        """
        files = _scan_files()
        assert len(files) >= 105, f"扫描面不足：{len(files)}（期望 ≥105）"
        problems = _hardcoded_drive_violations(files)
        print(f"\n[drive-guard] 扫描 {len(files)} 文件 ⇒ {len(problems)} 违例")
        assert not problems, "硬编码盘符违例：\n" + "\n".join(problems)

    def test_runs_dir_under_output(self):
        assert paths.RUNS_DIR.parent == paths.OUTPUT_DIR

@pytest.mark.smoke
def test_guard_catches_hardcoded_drive(tmp_path):
    """守卫必须能红：临时文件注入 `X = r"D:\\foo"` ⇒ 必须检出（19 号 §159 D-4）。"""
    bad = tmp_path / "bad_drive.py"
    bad.write_text('X = r"D:\\foo"\n', encoding="utf-8")
    problems = _hardcoded_drive_violations([bad])
    print("\n[reverse] 注入 D:\\ 违例 ⇒ "
          + ("\n".join(problems) if problems else "未检出"))
    assert problems, "守卫未检出硬编码盘符（失效）"
    assert "bad_drive.py" in problems[0]
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
