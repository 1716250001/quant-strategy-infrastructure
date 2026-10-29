# -*- coding: utf-8 -*-
"""L1 单元：**A-1-T1** `_sample_hash` 双路径等价（19 号 §181 A-1）。

补丁背景：`_sample_hash` 原先把整列 `to_pylist()` 再逐值取样本（130 万行只用
131 个值）；改为接受 Arrow 列、只 `take` 抽样行。**哈希必须逐字节不变**——
本文件钉死「Arrow 入参 ≡ list 入参」，覆盖空列／单行／恰好 stride 倍数／
多 chunk（ChunkedArray）。
"""
from __future__ import annotations

import hashlib

import pyarrow as pa
import pytest
from btf.data.version import _SAMPLE_STRIDE, _sample_hash

pytestmark = [pytest.mark.l1]


def _both(values: pa.Array | pa.ChunkedArray) -> tuple[str, str]:
    """同一列的两条路径：Arrow 列 vs 已物化 list。"""
    return _sample_hash(values), _sample_hash(values.to_pylist())


def test_empty_column_untruncated_and_equal():
    """空列：两条路径同值，且**返回未截断**的全长哈希（红线：不得顺手改 `[:32]`）。"""
    col = pa.array([], type=pa.string())
    a, b = _both(col)
    assert a == b == hashlib.sha256().hexdigest()
    assert len(a) == 64


def test_single_row_equal():
    col = pa.array(["20240102"])
    a, b = _both(col)
    assert a == b


@pytest.mark.parametrize("n", [2, 10, 9972, _SAMPLE_STRIDE, _SAMPLE_STRIDE + 1])
def test_sample_boundaries_equal(n: int):
    """首末 + 每 stride 行的抽样索引在 Arrow 与 list 上必须一致（含 stride 倍数边界）。"""
    col = pa.array([f"2024{i:04d}" for i in range(n)])
    a, b = _both(col)
    assert a == b


def test_multi_chunk_equals_single_chunk():
    """ChunkedArray（多 row group 拼接）与单 chunk 结果同值（跨文件累积语义）。"""
    t1 = pa.table({"c": pa.array(["a", "b", "c"])})
    t2 = pa.table({"c": pa.array(["d", "e"])})
    chunked = pa.concat_tables([t1, t2]).column("c")
    assert chunked.num_chunks == 2
    assert _sample_hash(chunked) == _sample_hash(chunked.to_pylist())


def test_custom_stride_equal():
    """小 stride（覆盖更多样本）时两条路径仍同值。"""
    col = pa.array([i % 7 for i in range(20)])
    assert _sample_hash(col, stride=3) == _sample_hash(col.to_pylist(), stride=3)
