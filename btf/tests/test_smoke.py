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
        """paths.py 源码中禁止硬编码盘符（ADR-4 的机检落地）。

        检查逻辑：跳过 docstring 块（状态机）与行内注释（剥离 ` # ` 之后），
        仅对可执行代码断言不含 "D:\\" / "C:\\" 字面量。
        """
        src = Path(paths.__file__).read_text(encoding="utf-8")
        in_doc = False
        for raw in src.splitlines():
            line = raw.strip()
            if not line:
                continue
            # docstring 状态机：首尾行翻转；docstring 内部行跳过
            if line.startswith(('"""', "'''")):
                quote = line[:3]
                if line.endswith(quote) and len(line) > 3:
                    continue  # 单行 docstring
                in_doc = not in_doc
                continue
            if in_doc or line.startswith("#"):
                continue
            # 剥离行内注释（保守：仅认 " # " 形式）
            code = raw.split(" # ", 1)[0]
            assert "D:\\" not in code, f"硬编码盘符违例: {code}"
            assert "C:\\" not in code, f"硬编码盘符违例: {code}"

    def test_runs_dir_under_output(self):
        assert paths.RUNS_DIR.parent == paths.OUTPUT_DIR


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
