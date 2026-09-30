# -*- coding: utf-8 -*-
"""跨版本源码冷备单测（裁决五-1/六-2；19 号 §37.4.2）。

铁律新 15（防线可被证伪）：本文件对冷备**自身**做变异测试——
篡改副本文件后 `verify` 必须变红；缺失文件必须报缺；正常冷备必须逐文件一致。
铁律新 16（披露不空）：`MANIFEST.json` 的版本/文件数/字节数/哈希
必须取到真值（不得恒空）。
"""
from __future__ import annotations

import json
import shutil

import pytest

import cold_backup as cb


@pytest.fixture()
def fake_repo(tmp_path, monkeypatch):
    """合成最小仓库：version 文件 + 两个源码文件 + 一个派生目录。"""
    repo = tmp_path / "btf_repo"
    (repo / "btf").mkdir(parents=True)
    (repo / "btf" / "_version.py").write_text(
        '__version__ = "9.9.9"\n', encoding="utf-8")
    (repo / "btf" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "btf" / "__pycache__").mkdir()
    (repo / "btf" / "__pycache__" / "mod.pyc").write_bytes(b"\x00")
    (repo / "CHANGELOG.md").write_text("# changelog\n", encoding="utf-8")
    monkeypatch.setattr(cb, "ROOT", repo)
    monkeypatch.setattr(cb, "INCLUDE", ("btf", "CHANGELOG.md"))
    return repo


class TestBackup:
    def test_manifest_has_real_values(self, fake_repo, tmp_path):
        """① 冷备成功 + MANIFEST 取到真值（版本/计数/字节/哈希非空）。"""
        target = cb.backup(tmp_path / "backups", "单测")
        manifest = json.loads(
            (target / "MANIFEST.json").read_text(encoding="utf-8"))
        assert manifest["version"] == "9.9.9"
        assert manifest["file_count"] == 3          # _version.py/mod.py/CHANGELOG
        assert manifest["total_bytes"] > 0
        assert all(len(e["sha256"]) == 64 for e in manifest["files"])
        assert (target / "btf-src" / "btf" / "mod.py").is_file()
        assert (target / "SHA256SUMS.txt").is_file()
        assert (target / "COLD_BACKUP.md").is_file()

    def test_excludes_derived_dirs(self, fake_repo, tmp_path):
        """② 派生目录（__pycache__）不进入冷备。"""
        target = cb.backup(tmp_path / "backups", "单测")
        assert not (target / "btf-src" / "btf" / "__pycache__").exists()
        manifest = json.loads(
            (target / "MANIFEST.json").read_text(encoding="utf-8"))
        assert all("__pycache__" not in e["file"] for e in manifest["files"])

    def test_refuses_overwrite(self, fake_repo, tmp_path, monkeypatch):
        """③ 目标已存在 → 拒绝覆盖（fail-closed）。"""

        real = cb.datetime

        class _FrozenDatetime:
            @staticmethod
            def now():
                return real(2026, 9, 29, 1, 2)

        monkeypatch.setattr(cb, "datetime", _FrozenDatetime)
        out = tmp_path / "backups"
        cb.backup(out, "第一次")
        with pytest.raises(SystemExit):          # 同戳 → 同名目录 → 拒绝
            cb.backup(out, "第二次")


class TestVerifyMutation:
    """变异测试：证明校验不是伪防线。"""

    def test_verify_clean_passes(self, fake_repo, tmp_path):
        target = cb.backup(tmp_path / "backups", "单测")
        assert cb.verify(target) == []

    def test_verify_detects_tamper(self, fake_repo, tmp_path):
        target = cb.backup(tmp_path / "backups", "单测")
        victim = target / "btf-src" / "btf" / "mod.py"
        victim.write_text("X = 999   # 篡改\n", encoding="utf-8")
        problems = cb.verify(target)
        assert any("哈希不符" in p for p in problems), problems

    def test_verify_detects_missing(self, fake_repo, tmp_path):
        target = cb.backup(tmp_path / "backups", "单测")
        (target / "btf-src" / "CHANGELOG.md").unlink()
        problems = cb.verify(target)
        assert any("缺失" in p for p in problems), problems

    def test_main_verify_exit_codes(self, fake_repo, tmp_path, capsys):
        """CLI：干净 → 0；篡改 → 1（退出码即验收结论）。"""
        target = cb.backup(tmp_path / "backups", "单测")
        assert cb.main(["--verify", str(target)]) == 0
        shutil.copy2(tmp_path / "btf_repo" / "btf" / "mod.py",
                     target / "btf-src" / "btf" / "mod.py")
        (target / "btf-src" / "btf" / "mod.py").write_text("X = 2\n",
                                                           encoding="utf-8")
        assert cb.main(["--verify", str(target)]) == 1
