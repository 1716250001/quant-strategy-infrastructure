# -*- coding: utf-8 -*-
"""L2 集成：**A-1-T2** `content_hash` 不变（19 号 §181 A-1）。

固定 tmp 数据集（6000 行 × 3 列，`daily/2024.parquet`），指纹取 **fast 档**
（仅 `trade_date`/`ts_code` 两列）。常量在**改动前**用原实算取得：

    DIGEST = sha256:55b2980788c80afa   SAMPLE = 3226634d6e1d0ef9d309a3979c6b3fca

改后 `compute` 必须逐字节复现同一 digest —— 这是 A-1 的唯一硬条件
（`bt run --dry-run` 的既有 `expected_hash` 门禁是端到端复核）。
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.data.version import compute

pytestmark = [pytest.mark.l2]

ROWS = 6000
DIGEST_BEFORE = "sha256:55b2980788c80afa"
SAMPLE_BEFORE = "3226634d6e1d0ef9d309a3979c6b3fca"


@pytest.fixture()
def dataset(tmp_path: Path) -> Path:
    day = [i // 150 for i in range(ROWS)]          # 40 个交易日
    ts = [f"{i % 50:06d}.SZ" for i in range(ROWS)]
    ymd = [f"2024{1 + d // 28:02d}{1 + d % 28:02d}" for d in day]
    close = [i * 0.5 for i in range(ROWS)]
    t = pa.table({"ts_code": pa.array(ts), "trade_date": pa.array(ymd),
                  "close": pa.array(close, type=pa.float64())})
    (tmp_path / "daily").mkdir()
    pq.write_table(t, tmp_path / "daily" / "2024.parquet")
    return tmp_path


def test_content_hash_unchanged_after_a1(dataset):
    fp = compute(["daily"], dataset, mode="fast")
    assert fp.tables["daily"].row_count == ROWS
    assert fp.tables["daily"].sample_hash == SAMPLE_BEFORE
    assert fp.digest == DIGEST_BEFORE
