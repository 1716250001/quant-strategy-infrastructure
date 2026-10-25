# -*- coding: utf-8 -*-
"""L1 单测：数据冻结清单（**CL-4**，19 号 §72.5 S-2）——纯逻辑层。

钉住判据：
  A 字段完整（`anchor_date` / `tables`（名＋行数）/ `content_hash` / `mode` /
    `created_at`）；`content_hash` **原样透传** BB-1 实算值（R2 单一真值源）；
  B 同 `anchor_date` 重复冻结 ⇒ **幂等**（不覆写、内容不变）；哈希不同 ⇒
    **显式冲突**（R3：不静默覆写、不追加版本）；
  C 落盘后**只读**（`os.access(W_OK)` False），且回测/工具不修改既有清单。

真入口（含主库实算同源核对）见 `tests/integration/test_dataset_freeze_l4_cl4.py`。
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from btf.app.services import freeze_payload, write_freeze_manifest
from btf.config.paths import MARKET_DATA_DIR

pytestmark = [pytest.mark.l1]


def _info(hash_: str = "sha256:abc123", anchor: str = "2024-06-30") -> dict:
    return {
        "content_hash": hash_,
        "tables": ["daily", "stock_basic"],
        "anchor_date": anchor,
        "mode": "meta",
        "tables_detail": {
            "daily": {"table": "daily", "row_count": 1234,
                      "date_min": "2024-01-02", "date_max": "2024-06-28",
                      "sample_hash": "x"},
            "stock_basic": {"table": "stock_basic", "row_count": 5911,
                            "date_min": None, "date_max": None,
                            "sample_hash": "y"},
        },
    }


@pytest.fixture()
def _freeze_dir(tmp_path: Path) -> Path:
    """清单目录 + 测试后复位只读位（Windows 清理纪律：只读文件不可直接删）。"""
    out = tmp_path / "freeze"
    yield out
    if out.exists():
        for p in out.iterdir():
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


class TestFreezePayloadCl4:
    def test_fields_complete_and_hash_passthrough(self):
        """判据 A：R1 五字段齐备；`content_hash` 与 BB-1 实算**逐字同源**。"""
        payload = freeze_payload(_info(), period={"start": "2024-01-01",
                                                  "end": "2024-06-30"},
                                 root=str(MARKET_DATA_DIR))
        for key in ("anchor_date", "tables", "content_hash", "mode",
                    "created_at"):
            assert payload.get(key) not in (None, "", []), key
        assert payload["content_hash"] == "sha256:abc123"
        assert payload["anchor_date"] == "2024-06-30"
        # tables = 名 ＋ 行数（＋日期域）；排序确定
        assert [t["name"] for t in payload["tables"]] == ["daily", "stock_basic"]
        assert payload["tables"][0]["rows"] == 1234
        assert payload["tables"][0]["date_max"] == "2024-06-28"
        # 有效边界注（S-2 固有风险）与同源声明必须在案
        assert "依赖原始库不被改写" in payload["validity"]
        assert "BB-1" in payload["source"]


class TestFreezeWriteCl4:
    def test_created_then_readonly(self, _freeze_dir: Path):
        """判据 C：首建 ⇒ `created`；文件**只读**；内容=载荷。"""
        result = write_freeze_manifest(
            freeze_payload(_info(), period={"start": "a", "end": "b"},
                           root="r"), _freeze_dir)
        assert result["action"] == "created"
        path = result["path"]
        assert path.name == "2024-06-30.json"
        assert not os.access(path, os.W_OK), "冻结清单未置只读（R3）"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["content_hash"] == "sha256:abc123"
        assert data["schema"] == "freeze.v1"

    def test_same_anchor_same_hash_is_idempotent(self, _freeze_dir: Path):
        """判据 B（幂等）：同 anchor ＋ 同哈希 ⇒ 不覆写、内容逐字不变。"""
        first = write_freeze_manifest(
            freeze_payload(_info(), period={"start": "a", "end": "b"},
                           root="r"), _freeze_dir)
        before = first["path"].read_bytes()
        second = write_freeze_manifest(
            freeze_payload(_info(), period={"start": "a", "end": "b"},
                           root="r"), _freeze_dir)
        assert second["action"] == "idempotent"
        assert second["path"] == first["path"]
        assert first["path"].read_bytes() == before, "幂等路径被覆写（R3 违例）"

    def test_same_anchor_diff_hash_conflicts(self, _freeze_dir: Path):
        """判据 B（冲突）：同 anchor 不同哈希 ⇒ **显式冲突**、旧清单不动。"""
        first = write_freeze_manifest(
            freeze_payload(_info(), period={"start": "a", "end": "b"},
                           root="r"), _freeze_dir)
        before = first["path"].read_text(encoding="utf-8")
        with pytest.raises(FileExistsError, match="冻结冲突"):
            write_freeze_manifest(
                freeze_payload(_info(hash_="sha256:DIFFERENT"),
                               period={"start": "a", "end": "b"}, root="r"),
                _freeze_dir)
        assert first["path"].read_text(encoding="utf-8") == before

    def test_distinct_anchor_two_files(self, _freeze_dir: Path):
        """不同 anchor ⇒ 两份可见差异的清单（判据 B 前段）。"""
        a = write_freeze_manifest(
            freeze_payload(_info(anchor="2024-06-30"),
                           period={"start": "a", "end": "b"}, root="r"),
            _freeze_dir)
        b = write_freeze_manifest(
            freeze_payload(_info(hash_="sha256:second", anchor="2023-12-29"),
                           period={"start": "a", "end": "b"}, root="r"),
            _freeze_dir)
        assert a["path"].name == "2024-06-30.json"
        assert b["path"].name == "2023-12-29.json"
        assert json.loads(b["path"].read_text(encoding="utf-8"))[
            "content_hash"] == "sha256:second"


class TestFreezeRejectsMemoryFeedCl4:
    def test_memory_feed_rejected_exit_one(self, tmp_path: Path):
        """内存 Feed（无文件指纹）⇒ **显式报错**（退出 1），不产出假清单
        （铁律新 16：不得静默回落占位）。"""
        import yaml
        from btf import registry
        from btf.cli.main import main
        from btf.domain.contracts import CONTRACT_VERSION

        from tests.fixtures.scenarios import t_plus1_scenario

        feed = t_plus1_scenario().feed

        def factory(**_params):
            return feed

        factory.contract_version = CONTRACT_VERSION
        name = "freeze_reject_memory_feed"
        if name not in registry.available(registry.DATA_FEED):
            registry.register(registry.DATA_FEED, name, factory)
        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "000001.SZ", "weight": 0.9},
                "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
                "period": {"start": "2015-01-05", "end": "2015-01-09"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": name},
            "risk": {"rules": [], "allow_empty_chain": True},
        }
        path = tmp_path / "mem.yaml"
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True),
                        encoding="utf-8")
        fdir = tmp_path / "freeze"
        assert main(["dataset", "freeze", "--config", str(path),
                     "--out", str(fdir)]) == 1
        assert not fdir.exists() or not list(fdir.iterdir())
