# -*- coding: utf-8 -*-
"""bt config-check 命令单测（M1 任务 4.3 验收）。"""
from __future__ import annotations

import pytest
from btf.cli.main import main

from tests.unit.test_config import SAMPLE_06


@pytest.fixture()
def sample_yaml(tmp_path):
    p = tmp_path / "backtest.yaml"
    p.write_text(SAMPLE_06, encoding="utf-8")
    return p


class TestConfigCheck:
    def test_valid_config_exits_zero(self, sample_yaml, capsys):
        rc = main(["config-check", str(sample_yaml)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "backtest.v1 校验通过" in out
        assert "myapp.strategies:RotationStrategy" in out
        assert "2018-01-01 → 2024-12-31" in out

    def test_invalid_config_exits_nonzero(self, tmp_path, capsys):
        p = tmp_path / "bad.yaml"
        p.write_text(SAMPLE_06.replace("source: \"index\"", "source: 123"),
                     encoding="utf-8")
        rc = main(["config-check", str(p)])
        err = capsys.readouterr().err
        assert rc == 1
        assert "校验失败" in err

    def test_h2_literal_rejected_by_cli(self, tmp_path, capsys):
        p = tmp_path / "h2.yaml"
        p.write_text(SAMPLE_06.replace(
            "  overrides: []",
            "  overrides: []\n  params: {pe_ttm_max: 30}"), encoding="utf-8")
        rc = main(["config-check", str(p)])
        err = capsys.readouterr().err
        assert rc == 1
        assert "'params' was unexpected" in err

    def test_dump_masks_sensitive(self, tmp_path, capsys):
        p = tmp_path / "secret.yaml"
        p.write_text(SAMPLE_06.replace(
            '  feed: "tushare_parquet"',
            '  feed: "tushare_parquet"\n  feed_params: {tushare_token: "REAL"}'),
            encoding="utf-8")
        rc = main(["config-check", str(p), "--dump"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "REAL" not in out
        assert "tushare_token: '***'" in out

    def test_missing_file_exits_nonzero(self, tmp_path, capsys):
        rc = main(["config-check", str(tmp_path / "ghost.yaml")])
        assert rc == 1
        assert "不可读" in capsys.readouterr().err
