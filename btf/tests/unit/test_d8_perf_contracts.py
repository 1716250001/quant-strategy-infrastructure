# -*- coding: utf-8 -*-
"""D8 性能契约：缓存数据版本键 + `persist=False` 跳过装配期质检。

- 缓存：主库 parquet 被改写后，`parquet_reader._CACHE` 必须返回新值
  （键含 `(size, mtime_ns)`）；`adjust/feed` 的共享缓存键含 `db_version` 戳。
- 质检：`BTFRuntime.build(quality=False)` 不调用 `run_data_quality`
  （网格每组合 `persist=False`，质检只对落盘产物有意义）。
"""

from __future__ import annotations

import gc
import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
from btf import registry
from btf.data import parquet_reader as pr
from btf.domain.contracts import CONTRACT_VERSION
from btf.runtime import BTFRuntime

from tests.fixtures.scenarios import t_plus1_scenario

FEED_NAME = "d8_quality_probe_feed"


def _write_close(path, value: float) -> None:
    """原子写入单行 daily 年文件（tmp + replace）。"""
    table = pa.table({"ts_code": ["000001.SZ"], "trade_date": ["20160104"], "close": [value]})
    tmp = path.with_suffix(".tmp.parquet")
    pq.write_table(table, tmp)
    os.replace(tmp, path)


def test_parquet_cache_invalidates_on_file_rewrite(tmp_path, monkeypatch):
    """D8：改写同一路径的 parquet 后，读取必须返回新值（不得命中旧缓存）。"""
    monkeypatch.setattr("btf.config.paths.MARKET_DATA_DIR", str(tmp_path))
    path = tmp_path / "daily" / "2016.parquet"
    path.parent.mkdir(parents=True)
    _write_close(path, 1.0)

    first = pr._read(path, columns=["ts_code", "close"])
    assert first.column("close").to_pylist() == [1.0]
    # 第二次命中缓存（同一版本键）
    second = pr._read(path, columns=["ts_code", "close"])
    assert second.column("close").to_pylist() == [1.0]
    del first, second
    gc.collect()  # 释放 memory_map，允许 Windows 覆盖

    _write_close(path, 2.0)
    third = pr._read(path, columns=["ts_code", "close"])
    assert third.column("close").to_pylist() == [2.0], (
        "文件改写后仍返回旧值——缓存键未包含 (size, mtime_ns)"
    )


def test_shared_cache_stamp_tracks_db_version(tmp_path):
    """D8：`db_version.json` 更新后版本戳变化（adjust/feed 共享缓存键依据）。"""
    meta = tmp_path / "metadata"
    meta.mkdir()
    version = meta / "db_version.json"

    assert pr.data_stamp(tmp_path) == (0, 0, "")
    version.write_text(json.dumps({"version": "MD-20260101.1"}), encoding="utf-8")
    first = pr.data_stamp(tmp_path)
    assert first[2] == "MD-20260101.1"

    version.write_text(json.dumps({"version": "MD-20260101.2"}), encoding="utf-8")
    second = pr.data_stamp(tmp_path)
    assert second != first
    assert second[2] == "MD-20260101.2"


def test_build_quality_false_skips_assembly_quality(monkeypatch):
    """D8：`build(quality=False)` 不得调用装配期质检。"""
    from btf.runtime.component_assembler import ComponentAssembler

    feed = t_plus1_scenario().feed

    def factory(**_params):
        return feed

    factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, factory)
    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
            "period": {"start": "2015-01-05", "end": "2015-01-09"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": FEED_NAME},
        "risk": {"rules": [], "allow_empty_chain": True},
    }
    calls: list[object] = []
    monkeypatch.setattr(
        ComponentAssembler,
        "run_data_quality",
        lambda self, period: calls.append(period) or {"enabled": True},
    )

    rt = BTFRuntime().load_config(cfg).build(quality=False)
    assert calls == []
    assert rt.data_quality == {}
