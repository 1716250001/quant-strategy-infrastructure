# -*- coding: utf-8 -*-
"""`bt dataset refresh-hash` 回归（G3/D7；B-P1-4）。

四类硬约束（`app/refresh_hash.py`）：
    1. 行级替换——只改 `expected_hash` 行的哈希令牌，注释/键序零变化；
    2. 逐文件备份——写前 `<name>.bak-<ts>`，原字节可回滚；
    3. BOM 保留——UTF-8 BOM 文件写回后首字节仍为 `EF BB BF`；
    4. 幂等——值已最新 ⇒ 报 current，文件字节不变、不再生成备份。
另含 `--dry-run` 不写盘。

测试桩掉 `_compute_hash`（不装配主库），只验证改写/备份/编码/幂等行为。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from btf.app import refresh_hash

pytestmark = [pytest.mark.l1]

OLD = "sha256:1111111111111111"
NEW = "sha256:2222222222222222"
YAML = (
    "# 头部注释：保持原样\n"
    "schema_version: backtest.v1\n"
    "data:\n"
    "  feed: tushare_parquet\n"
    "  dataset:\n"
    f'    expected_hash: "{OLD}"   # 行尾注释保留\n'
    "    anchor_date: 2024-12-31\n"
    "run:\n"
    "  period: {start: 2024-01-02, end: 2024-12-31}\n"
)


@pytest.fixture()
def cfg(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(YAML, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fake_hash(monkeypatch):
    monkeypatch.setattr(refresh_hash, "_compute_hash", lambda *_a, **_k: (NEW, "meta"))


def test_line_level_replacement(cfg: Path, tmp_path: Path):
    backup_dir = tmp_path / "bak"
    row = refresh_hash.refresh_hash_one(cfg, backup_dir=backup_dir)
    assert row["status"] == "updated", row
    assert row["old"] == OLD and row["new"] == NEW and row["mode"] == "meta"
    before = YAML.splitlines()
    after = cfg.read_text(encoding="utf-8").splitlines()
    diff = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert len(diff) == 1  # 仅 expected_hash 行
    assert OLD not in after[diff[0]] and NEW in after[diff[0]]
    assert after[diff[0]].endswith("# 行尾注释保留")
    assert after[0] == "# 头部注释：保持原样"  # 注释/键序零变化
    assert after[-1] == before[-1]


def test_backup_written_with_original_bytes(cfg: Path, tmp_path: Path):
    backup_dir = tmp_path / "bak"
    row = refresh_hash.refresh_hash_one(cfg, backup_dir=backup_dir)
    assert row["status"] == "updated"
    baks = list(backup_dir.glob("config.yaml.bak-*"))
    assert len(baks) == 1
    assert baks[0].read_text(encoding="utf-8") == YAML


def test_bom_preserved(tmp_path: Path):
    """BOM 文件写回后仍带 BOM（且令牌被替换）。"""
    path = tmp_path / "bom.yaml"
    path.write_bytes(b"\xef\xbb\xbf" + YAML.encode("utf-8"))
    row = refresh_hash.refresh_hash_one(path, backup_dir=tmp_path / "bak")
    assert row["status"] == "updated", row
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert NEW.encode() in raw and OLD.encode() not in raw


def test_idempotent_second_run(cfg: Path, tmp_path: Path):
    backup_dir = tmp_path / "bak"
    first = refresh_hash.refresh_hash_one(cfg, backup_dir=backup_dir)
    assert first["status"] == "updated"
    data_after_first = cfg.read_bytes()
    baks_after_first = list(backup_dir.glob("config.yaml.bak-*"))
    second = refresh_hash.refresh_hash_one(cfg, backup_dir=backup_dir)
    assert second["status"] == "current", second
    assert second["old"] == second["new"] == NEW
    assert cfg.read_bytes() == data_after_first  # 字节不变
    assert list(backup_dir.glob("config.yaml.bak-*")) == baks_after_first


def test_dry_run_no_write(cfg: Path, tmp_path: Path):
    backup_dir = tmp_path / "bak"
    row = refresh_hash.refresh_hash_one(cfg, dry_run=True, backup_dir=backup_dir)
    assert row["status"] == "dry-run"
    assert cfg.read_text(encoding="utf-8") == YAML  # 未写盘
    assert not backup_dir.exists()


def test_missing_expected_hash_skipped(tmp_path: Path):
    path = tmp_path / "plain.yaml"
    path.write_text("schema_version: backtest.v1\n", encoding="utf-8")
    row = refresh_hash.refresh_hash_one(path)
    assert row["status"] == "skipped"
    assert path.read_text(encoding="utf-8") == "schema_version: backtest.v1\n"
