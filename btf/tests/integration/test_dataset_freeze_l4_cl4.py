# -*- coding: utf-8 -*-
"""L4 集成（主库依赖）：`bt dataset freeze` 端到端——**CL-4**（19 号 §72.5）。

判据（审查方独立复跑锚点）：
  A 冻结清单含 R1 全字段；`content_hash` 与 **BB-1 实算同源同值**——这里用
    「同配置 `bt run` 的 `manifest.data_version.content_hash` 逐字相等」钉住；
  B 同 `anchor_date` 重复冻结 ⇒ 幂等（不覆写；R3 声明见 18 号 §九·V）；
  C 清单文件**只读**；
  D 退出码：成功 0 / 冲突 1（本文件覆盖 0 与幂等；冲突见 L1 单测）。

注意：`bt dataset freeze` 不跑回测（只 build 装配 + 指纹实算）。
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR

pytestmark = [pytest.mark.l4]

ROOT = Path(MARKET_DATA_DIR)
requires_mainlib = pytest.mark.skipif(
    not (ROOT / "daily").is_dir(), reason=f"主库不可用: {ROOT}")


def _write_config(tmp_path: Path) -> Path:
    import yaml

    cfg = {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": "000001.SZ", "weight": 0.9},
            "universe": {"source": "explicit",
                         "symbols": ["000001.SZ", "600000.SH"]},
            "period": {"start": "2024-01-01", "end": "2024-06-30"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": "tushare_parquet"},
        "risk": {"rules": [], "allow_empty_chain": True},
    }
    path = tmp_path / "freeze.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture()
def _writable(tmp_path: Path) -> Path:
    """tmp_path + 收尾复位只读位（Windows 下只读文件不可直接清理）。"""
    yield tmp_path
    fdir = tmp_path / "freeze"
    if fdir.exists():
        for p in fdir.iterdir():
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


@requires_mainlib
class TestDatasetFreezeEndToEnd:
    def test_freeze_same_hash_as_run_manifest(self, _writable: Path):
        """判据 A：freeze 清单哈希 == 同配置 run 的 manifest 指纹（同源）。"""
        from btf.cli.main import main

        tmp_path = _writable
        cfg = _write_config(tmp_path)
        fdir = tmp_path / "freeze"
        assert main(["dataset", "freeze", "--config", str(cfg),
                     "--out", str(fdir)]) == 0
        files = list(fdir.iterdir())
        assert len(files) == 1, f"冻结目录应恰一份清单：{files}"
        frozen = json.loads(files[0].read_text(encoding="utf-8"))

        for key in ("anchor_date", "tables", "content_hash", "mode",
                    "created_at"):
            assert frozen.get(key) not in (None, "", []), key
        assert frozen["content_hash"].startswith("sha256:")
        assert all({"name", "rows"} <= set(t) for t in frozen["tables"])

        out_dir = tmp_path / "runs"
        assert main(["run", "--config", str(cfg),
                     "--out", str(out_dir)]) == 0
        run_dir = next(p for p in out_dir.iterdir() if p.is_dir())
        manifest = json.loads((run_dir / "manifest.json").read_text(
            encoding="utf-8"))
        dv = manifest["data_version"]
        assert frozen["content_hash"] == dv["content_hash"], (
            "冻结清单与 manifest 指纹不同源（R2 违例）")
        assert frozen["anchor_date"] == dv["anchor_date"]

    def test_readonly_and_idempotent_refreeze(self, _writable: Path):
        """判据 B/C：文件只读；复冻幂等（内容逐字不变）。"""
        from btf.cli.main import main

        tmp_path = _writable
        cfg = _write_config(tmp_path)
        fdir = tmp_path / "freeze"
        assert main(["dataset", "freeze", "--config", str(cfg),
                     "--out", str(fdir)]) == 0
        path = next(iter(fdir.iterdir()))
        assert not os.access(path, os.W_OK), "冻结清单未置只读（R3）"
        before = path.read_bytes()

        assert main(["dataset", "freeze", "--config", str(cfg),
                     "--out", str(fdir)]) == 0
        assert path.read_bytes() == before, "幂等复冻覆写了清单（R3 违例）"
        assert len(list(fdir.iterdir())) == 1
