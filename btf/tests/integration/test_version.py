# -*- coding: utf-8 -*-
"""L2 集成测试：DataVersion 指纹（PoC-1 任务 1.7 验收）。

验收锚点（18 号计划 1.7 / 11 §21.1）：
    - 同参数重复调用一致（确定性）
    - 数据变化→指纹变化（修正敏感性；用 tmp 目录模拟）
    - fast 档耗时实测（全库 [待验证] 项落定的证据）
"""
from __future__ import annotations

import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.version import compute

pytestmark = [pytest.mark.l2]

ROOT = Path(MARKET_DATA_DIR)


def _skip_if_no_data():
    if not (ROOT / "daily").is_dir():
        pytest.skip("主库不可用")


class TestDeterminism:
    def test_repeat_calls_identical(self):
        _skip_if_no_data()
        f1 = compute(["stk_limit", "suspend_d"], ROOT)
        f2 = compute(["stk_limit", "suspend_d"], ROOT)
        assert f1.digest == f2.digest
        assert f1.as_dict() == f2.as_dict()

    def test_known_counts(self):
        """行数与实测锚点一致（trade_cal=13,527）。"""
        _skip_if_no_data()
        fp = compute(["trade_cal"], ROOT)
        assert fp.tables["trade_cal"].row_count == 13527


class TestSensitivity:
    def _make_fake(self, tmp_path: Path) -> Path:
        d = tmp_path / "stk_limit"
        d.mkdir()
        t = pa.table({
            "ts_code": ["000001.SZ", "000002.SZ"],
            "trade_date": ["20240102", "20240102"],
            "up_limit": [11.0, 12.0],
            "down_limit": [9.0, 10.0],
        })
        pq.write_table(t, d / "2024.parquet")
        return tmp_path

    def test_key_change_detected_by_fast(self, tmp_path: Path):
        """fast 档覆盖键列（date/ts_code）：键变化必须改变指纹。"""
        root = self._make_fake(tmp_path)
        d1 = compute(["stk_limit"], root).digest
        t = pq.read_table(root / "stk_limit" / "2024.parquet")
        t = t.set_column(
            t.schema.get_field_index("trade_date"), "trade_date",
            pa.array(["20240103", "20240102"]),
        )
        pq.write_table(t, root / "stk_limit" / "2024.parquet")
        d2 = compute(["stk_limit"], root).digest
        assert d1 != d2, "键列变化必须改变 fast 指纹"

    def test_value_change_detected_by_full(self, tmp_path: Path):
        """值级修正（如 gap-update 修 close）需 full 档才检出——08 §13.5
        修正检测的档位语义：fast=键/行数级，full=值级。"""
        root = self._make_fake(tmp_path)
        d1 = compute(["stk_limit"], root, mode="full").digest
        t = pq.read_table(root / "stk_limit" / "2024.parquet")
        t = t.set_column(
            t.schema.get_field_index("up_limit"), "up_limit",
            pa.array([11.5, 12.0]),
        )
        pq.write_table(t, root / "stk_limit" / "2024.parquet")
        d2 = compute(["stk_limit"], root, mode="full").digest
        assert d1 != d2, "值级修正必须改变 full 指纹"
        # 佐证：同变化对 fast 指纹不可见（设计取舍，登记于断言信息）
        # （fast 只读键列，正是 fast/full 两档存在的理由）

    def test_row_append_changes_digest(self, tmp_path: Path):
        root = self._make_fake(tmp_path)
        d1 = compute(["stk_limit"], root).digest
        t = pq.read_table(root / "stk_limit" / "2024.parquet")
        t = pa.concat_tables([t, pa.table({
            "ts_code": ["000003.SZ"], "trade_date": ["20240102"],
            "up_limit": [5.0], "down_limit": [4.0],
        })])
        pq.write_table(t, root / "stk_limit" / "2024.parquet")
        d2 = compute(["stk_limit"], root).digest
        assert d1 != d2


class TestTiming:
    def test_fast_mode_all_17_tables_timing(self):
        """全库 fast 指纹耗时实测（PoC-1 验证点：[待验证] 项落定）。"""
        _skip_if_no_data()
        from btf.data.tables_meta import TABLES

        t0 = time.perf_counter()
        fp = compute(sorted(TABLES), ROOT)
        elapsed = time.perf_counter() - t0
        print(f"\n全库 18 物理表 fast 指纹耗时: {elapsed:.2f}s, digest={fp.digest}")
        # 快速档的可用性门槛：全库指纹应分钟级内（锚点断言，超 10 分钟视为设计失效）
        assert elapsed < 600, f"fast 指纹 {elapsed:.1f}s 超可用上限"
        # 证据登记：daily 行数千万级
        assert fp.tables["daily"].row_count > 15_000_000
