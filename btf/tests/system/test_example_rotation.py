# -*- coding: utf-8 -*-
"""系统测试（L3）：示例策略 rotation.py 端到端首回测（18 号 5.10 DXP 验收）。

验收：
    1. 示例配置 `examples/config_rotation.yaml` 可直接跑（config-check → run）；
    2. 端到端耗时 **< 30 分钟**（DXP 验收口径；实测应远低于此）；
    3. 产物齐备（manifest 七件）+ R1 复核通过 + 报告可生成。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from btf.cli.main import main
from btf.config.paths import MARKET_DATA_DIR
from btf.runtime import BTFRuntime, make_store

pytestmark = [pytest.mark.l3]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG = PROJECT_ROOT / "examples" / "config_rotation.yaml"
DXP_LIMIT_SECONDS = 30 * 60          # 30 分钟（DXP 验收）


@pytest.mark.skipif(not (Path(MARKET_DATA_DIR) / "daily").is_dir(),
                    reason="主库不可用")
class TestExampleRotation:
    def test_config_check(self, capsys):
        assert main(["config-check", str(CONFIG)]) == 0
        assert "校验通过" in capsys.readouterr().out

    def test_first_backtest_end_to_end(self, tmp_path):
        """首回测：真实数据 → 落盘 → R1 复核 → 报告（< 30 分钟）。"""
        t0 = time.perf_counter()
        rt = BTFRuntime().load_config(CONFIG).build()
        store = make_store(tmp_path)
        result = rt.run(store=store)
        elapsed = time.perf_counter() - t0

        assert result.snapshots, "无快照（回测未产出）"
        assert result.fills, "无成交（示例策略应调仓）"
        assert elapsed < DXP_LIMIT_SECONDS, f"DXP 验收超标：{elapsed:.0f}s"
        print(f"\nDXP-rotation: {result.n_days} 日 / {len(result.fills)} 成交 "
              f"/ {elapsed:.1f}s（预算 {DXP_LIMIT_SECONDS}s）")

        # 产物七件齐全
        directory = store.run_dir(result.run_id)
        for name in ("manifest.json", "snapshots.jsonl", "trades.jsonl",
                     "fills.jsonl", "rejections.jsonl", "events.jsonl",
                     "metrics.json"):
            assert (directory / name).is_file(), name

        # R1 复核 + 报告再生成
        report = rt.verify_run(result.run_id, store=store)
        assert report["match"], "R1 复核失败（产物与 manifest 摘要不一致）"
        assert main(["report", "--run", result.run_id,
                     "--out-dir", str(tmp_path)]) == 0
        assert (directory / "report.html").is_file()
