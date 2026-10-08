# -*- coding: utf-8 -*-
"""CL-2-H（R34′）：`bt data scan-consolidation` 端到端（真实主库 + smoke 进 CI）。

判据（19 号 §96.4 R34′ / G-DOC-7 锚点）：510310.SH 2024-09-23
R = `pre_close÷前收` = **2.000625**（逐位）；双证据偏差 < 0.5%；退出码语义
0/1/2（本用例覆盖 0 与 2；fail-closed=1 的合成用例见
`tests/unit/test_etf_corp_actions_cl2h.py`）。
"""
from __future__ import annotations

import pathlib

import pytest
from btf.config.paths import MARKET_DATA_DIR

pytestmark = [pytest.mark.smoke]

_ROOT = pathlib.Path(MARKET_DATA_DIR)
_HAS_DATA = (_ROOT / "fund_daily").is_dir() and (_ROOT / "fund_adj").is_dir()
skip_no_data = pytest.mark.skipif(
    not _HAS_DATA, reason="主库缺失（fund_daily/fund_adj）——数据相关用例跳过")


def _write_cfg(tmp_path: pathlib.Path) -> pathlib.Path:
    cfg = tmp_path / "scan.yaml"
    cfg.write_text(
        "schema_version: \"backtest.v1\"\n"
        "run:\n"
        "  universe: {source: explicit, symbols: [\"510310.SH\"]}\n"
        "  period: {start: \"2024-01-01\", end: \"2024-12-31\"}\n",
        encoding="utf-8")
    return cfg


@skip_no_data
def test_scan_consolidation_anchor_510310(tmp_path):
    from btf.app import scan_consolidation

    report = scan_consolidation(_write_cfg(tmp_path))
    assert report["n_fail_closed"] == 0
    assert report["n_events"] == 1
    ev = report["events"][0]
    assert ev["ts_code"] == "510310.SH" and ev["date"] == "20240923"
    assert ev["R"] == pytest.approx(2.000625, abs=1e-6)   # G-DOC-7 锚点
    assert ev["ok"] is True
    assert ev["dev"] < 0.005


@skip_no_data
def test_scan_consolidation_usage_errors(tmp_path):
    from btf.app import scan_consolidation

    bad = tmp_path / "bad.yaml"
    bad.write_text("run: {universe: {source: all}}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        scan_consolidation(bad)
    with pytest.raises(FileNotFoundError):
        scan_consolidation(tmp_path / "missing.yaml")


@skip_no_data
def test_cli_exit_codes(tmp_path):
    from btf.cli.main import main

    cfg = _write_cfg(tmp_path)
    assert main(["data", "scan-consolidation", "--config", str(cfg)]) == 0
    assert main(["data", "scan-consolidation"]) == 2          # 缺 --config
    out = tmp_path / "scan.json"
    assert main(["data", "scan-consolidation", "--config", str(cfg),
                 "--out", str(out)]) == 0
    assert out.is_file()
