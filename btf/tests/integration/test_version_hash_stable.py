# -*- coding: utf-8 -*-
"""L2 集成：**A-1-T2 + G3/D6** `content_hash` 稳定性。

固定 tmp 数据集（6000 行 × 3 列，`daily/2024.parquet`），fast 档常量在
**A-1 改动前**用原实算取得：

    DIGEST = sha256:55b2980788c80afa   SAMPLE = 3226634d6e1d0ef9d309a3979c6b3fca

G3/D6 起 `meta` 档**剔除 mtime**（fingerprint_version=2）：
    · 同内容复制到新目录 / 改 mtime → meta 指纹必须一致；
    · 同内容重写年文件 → meta 指纹必须一致；
    · 仅改非 min/max 的抽样值 → meta 可不变，fast/full 必变（档位差异可证）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.data.version import compute

pytestmark = [pytest.mark.l2]

ROWS = 6000
DIGEST_BEFORE = "sha256:55b2980788c80afa"
SAMPLE_BEFORE = "3226634d6e1d0ef9d309a3979c6b3fca"


def _make_dataset(root: Path, rows: int = ROWS) -> Path:
    """固定规则数据集：40 个交易日 / 50 个代码 / close 线性。"""
    day = [i // 150 for i in range(rows)]  # 40 个交易日
    ts = [f"{i % 50:06d}.SZ" for i in range(rows)]
    ymd = [f"2024{1 + d // 28:02d}{1 + d % 28:02d}" for d in day]
    close = [i * 0.5 for i in range(rows)]
    t = pa.table(
        {
            "ts_code": pa.array(ts),
            "trade_date": pa.array(ymd),
            "close": pa.array(close, type=pa.float64()),
        }
    )
    (root / "daily").mkdir(parents=True)
    # 固定编码参数：同长度字符串替换后文件字节数保持一致（meta 档前提可证）
    pq.write_table(
        t,
        root / "daily" / "2024.parquet",
        compression="none",
        use_dictionary=False,
        data_page_version="1.0",
    )
    return root


@pytest.fixture()
def dataset(tmp_path: Path) -> Path:
    return _make_dataset(tmp_path)


def test_content_hash_unchanged_after_a1(dataset):
    fp = compute(["daily"], dataset, mode="fast")
    assert fp.tables["daily"].row_count == ROWS
    assert fp.tables["daily"].sample_hash == SAMPLE_BEFORE
    assert fp.digest == DIGEST_BEFORE
    assert fp.tables["daily"].fingerprint_version == 2


def test_meta_same_content_copied_dir_and_mtime(dataset, tmp_path):
    """同内容换目录 + 改 mtime ⇒ meta content_hash 一致（D6 硬条件）。"""
    fp1 = compute(["daily"], dataset, mode="meta")
    other = tmp_path / "复制目录"
    shutil.copytree(dataset, other)
    target = other / "daily" / "2024.parquet"
    st = target.stat()
    os.utime(target, (st.st_atime + 86400, st.st_mtime + 86400))
    fp2 = compute(["daily"], other, mode="meta")
    assert fp1.digest == fp2.digest
    assert fp1.tables["daily"].sample_hash == fp2.tables["daily"].sample_hash
    assert fp1.tables["daily"].fingerprint_version == 2


def test_meta_same_content_rewritten_year_file(dataset):
    """同内容重写年文件（不同 mtime、相同 size/rows/stats）⇒ meta 一致。"""
    path = dataset / "daily" / "2024.parquet"
    fp1 = compute(["daily"], dataset, mode="meta")
    size_before = path.stat().st_size
    t = pq.read_table(path)
    pq.write_table(t, path, compression="none", use_dictionary=False, data_page_version="1.0")
    size_after = path.stat().st_size
    assert size_after == size_before  # 元数据档前提：尺寸不变
    fp2 = compute(["daily"], dataset, mode="meta")
    assert fp1.digest == fp2.digest


def test_meta_ignores_non_extreme_value_but_fast_full_change(tmp_path):
    """改非 min/max 的抽样值：meta 不变；fast/full 必变（档位差异）。"""
    root = _make_dataset(tmp_path, rows=20_000)  # 抽样点含 9973（> stride）
    path = root / "daily" / "2024.parquet"
    meta1 = compute(["daily"], root, mode="meta")
    fast1 = compute(["daily"], root, mode="fast")
    full1 = compute(["daily"], root, mode="full")
    size_before = path.stat().st_size

    t = pq.read_table(path)
    dates = t.column("trade_date").to_pylist()
    idx = 9973
    old = dates[idx]
    new = "20240121" if old != "20240121" else "20240122"
    assert old != new and min(dates) <= new <= max(dates)
    dates[idx] = new
    t = t.set_column(t.schema.get_field_index("trade_date"), "trade_date", pa.array(dates))
    pq.write_table(t, path, compression="none", use_dictionary=False, data_page_version="1.0")
    assert path.stat().st_size == size_before  # 尺寸/行数/极值均未变

    meta2 = compute(["daily"], root, mode="meta")
    fast2 = compute(["daily"], root, mode="fast")
    full2 = compute(["daily"], root, mode="full")
    assert meta2.digest == meta1.digest  # 预期：meta 不变（极小概率漏检）
    assert fast2.digest != fast1.digest  # fast 必变
    assert full2.digest != full1.digest  # full 必变
