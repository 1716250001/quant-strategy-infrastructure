# -*- coding: utf-8 -*-
"""CLI 六命令族冒烟（M2 任务 5.9；18 号 5.9 验收）。

验收：
    1. 六命令登记（注册表式：加命令=加一行，不改动分发骨架）；
    2. `--dry-run` 语义明确：装配完成但**不建 run 目录、不落盘**；
    3. run → report → verify 链路端到端可跑通（tmp 产物目录）；
    4. 失败路径显式退出码（配置缺失/未知 run）。
"""
from __future__ import annotations

import json

import pytest
from btf import registry
from btf.cli.main import COMMANDS, main
from btf.domain.contracts import CONTRACT_VERSION

pytestmark = [pytest.mark.l1]

FEED_NAME = "cli_cmd_feed"


@pytest.fixture()
def config_path(tmp_path):
    """回测配置 YAML（MemoryFeed 注入 + 5 日区间）。"""
    from tests.fixtures.scenarios import t_plus1_scenario

    feed = t_plus1_scenario().feed

    def factory(**_params):
        return feed

    factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, factory)

    import yaml

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
        # X-7（Q1=A）：空风控链默认已改为**拒绝**——本用例不测风控，显式
        # 声明"裸奔调试意图"以保留原语义（默认层不再替测试放行）
        "risk": {"rules": [], "allow_empty_chain": True},
        "seed": {"master": 20260926},
    }
    path = tmp_path / "backtest.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


class TestCommandRegistry:
    def test_seven_commands_registered(self):
        assert set(COMMANDS) == {"config-check", "run", "report", "verify",
                                 "test", "dataset", "optimize"}

    def test_each_command_has_help_and_args(self):
        for name, (handler, help_text, add_args) in COMMANDS.items():
            assert callable(handler) and help_text and callable(add_args), name

    def test_no_command_help(self):
        with pytest.raises(SystemExit):
            main([])


class TestConfigCheck:
    def test_ok(self, config_path, capsys):
        assert main(["config-check", str(config_path)]) == 0
        out = capsys.readouterr().out
        assert "校验通过" in out and "ConfigBuyHold" in out

    def test_missing_file(self, tmp_path, capsys):
        assert main(["config-check", str(tmp_path / "nope.yaml")]) == 1


class TestRunCommand:
    def test_dry_run_writes_nothing(self, config_path, tmp_path, capsys):
        """--dry-run：装配完成，不建 run 目录、不落盘。"""
        out_dir = tmp_path / "runs"
        assert main(["run", "--config", str(config_path), "--dry-run",
                     "--out", str(out_dir)]) == 0
        text = capsys.readouterr().out
        assert "dry-run" in text and "装配完成" in text
        assert not out_dir.exists()          # 不落盘

    def test_run_creates_artifacts(self, config_path, tmp_path, capsys):
        out_dir = tmp_path / "runs"
        assert main(["run", "--config", str(config_path),
                     "--out", str(out_dir)]) == 0
        text = capsys.readouterr().out
        assert "run_id" in text
        runs = list(out_dir.iterdir())
        assert len(runs) == 1
        assert (runs[0] / "manifest.json").is_file()
        assert (runs[0] / "snapshots.jsonl").is_file()

    def test_bad_config_exits_nonzero(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("schema_version: nope\n", encoding="utf-8")
        assert main(["run", "--config", str(bad), "--dry-run"]) == 1

    def test_bad_override_exits_two(self, config_path):
        assert main(["run", "--config", str(config_path), "--set", "x"]) == 2


class TestReportAndVerify:
    def _run_id(self, config_path, out_dir) -> str:
        assert main(["run", "--config", str(config_path),
                     "--out", str(out_dir)]) == 0
        runs = sorted(p for p in out_dir.iterdir() if p.is_dir())
        return runs[-1].name

    def test_report_generates_html(self, config_path, tmp_path, capsys):
        out_dir = tmp_path / "runs"
        run_id = self._run_id(config_path, out_dir)
        assert main(["report", "--run", run_id, "--out-dir",
                     str(out_dir)]) == 0
        text = capsys.readouterr().out
        assert "报告" in text
        html = out_dir / run_id / "report.html"
        assert html.is_file() and html.stat().st_size > 10_000
        content = html.read_text(encoding="utf-8")
        assert 'id="assumptions"' in content          # 强制章节

    def test_verify_r1_pass(self, config_path, tmp_path, capsys):
        out_dir = tmp_path / "runs"
        run_id = self._run_id(config_path, out_dir)
        assert main(["verify", "--run", run_id, "--out-dir",
                     str(out_dir)]) == 0
        assert "R1 复核通过" in capsys.readouterr().out

    def test_verify_tampered_digest_fails(self, config_path, tmp_path):
        """篡改 metrics.json → 重算 digest 与 manifest 不符 → 退出 1。"""
        out_dir = tmp_path / "runs"
        run_id = self._run_id(config_path, out_dir)
        metrics_path = out_dir / run_id / "metrics.json"
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        payload["metrics"]["total_return"] = 0.999
        metrics_path.write_text(json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8")
        assert main(["verify", "--run", run_id, "--out-dir", str(out_dir)]) == 1

    def test_unknown_run_exits_one(self, tmp_path):
        assert main(["report", "--run", "nope", "--out-dir",
                     str(tmp_path / "runs")]) == 1
        assert main(["verify", "--run", "nope", "--out-dir",
                     str(tmp_path / "runs")]) == 1


class TestTestCommand:
    def test_runs_smoke_subset(self, capsys):
        """bt test --layer all --pattern smoke：只跑冒烟子集（秒级）。"""
        code = main(["test", "--layer", "all", "--pattern", "smoke",
                     "--path", "tests/test_smoke.py"])
        assert code == 0


class TestDatasetCommand:
    def test_list_subcommand(self):
        """dataset --list 委托构建脚本（子进程；输出由 5.6 测试覆盖，此处验退出码）。"""
        assert main(["dataset", "--list"]) == 0
