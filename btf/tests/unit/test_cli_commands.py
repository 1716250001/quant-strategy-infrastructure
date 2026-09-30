# -*- coding: utf-8 -*-
"""CLI 命令族冒烟（M2 任务 5.9；18 号 5.9 验收 + CLI 审查报告-20260930 收编）。

验收：
    1. 十一命令登记（注册表式：加命令=加一行，不改动分发骨架）；
    2. `--dry-run` 语义明确：装配完成但**不建 run 目录、不落盘**；
    3. run → report → verify 链路端到端可跑通（tmp 产物目录）；
    4. 失败路径显式退出码（配置缺失/未知 run）；
    5. **P0/P2 收编**：`bt --version`（版本单源）、`bt check`/`bt cold-backup`
       委托 `tools/*.py`（本文件只验**转发与退出码**，不在此跑真门禁）、
       `bt runs`/`bt show` 只读查询（含坏 manifest 可见性）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from btf import registry
from btf.cli.main import COMMANDS, main
from btf.domain.contracts import CONTRACT_VERSION

pytestmark = [pytest.mark.l1]

ROOT = Path(__file__).resolve().parents[2]

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
    def test_eleven_commands_registered(self):
        assert set(COMMANDS) == {"config-check", "run", "report", "verify",
                                 "test", "dataset", "optimize", "check",
                                 "cold-backup", "runs", "show"}

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

    def test_refcalc_action_forwards(self):
        """P3：`dataset refcalc` 转 `build_golden.py --refcalc`（内嵌数据重算）。

        退出码允许 0/1：本机有无已构建案例决定 ok/缺失，但**不许崩、不许
        静默**（缺失必须打印出来——与 `--verify` 需主库不同，本动作不读主库）。
        """
        code = main(["dataset", "refcalc", "--cases", "g1"])
        assert code in (0, 1)

    def test_unknown_action_rejected(self, capsys):
        """动作白名单（choices）：拼错动作必须是参数错误，不能退化成构建。"""
        with pytest.raises(SystemExit) as excinfo:
            main(["dataset", "refcal"])         # 拼写错
        assert excinfo.value.code == 2


class TestVersionFlag:
    """P1：`bt --version`（审查报告：`--version` 未定义）。"""

    def test_prints_single_source_version(self, capsys):
        from btf._version import __version__

        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0
        assert capsys.readouterr().out.strip() == f"btf {__version__}"


class TestGateAndBackupDelegation:
    """P0/P2：门禁与冷备**收编为子命令**——委托 `tools/` 单一真源。

    单测只验"转发到哪个脚本、参数怎么排、退出码怎么透传"；**真门禁不在此跑**
    （九项动辄分钟级，跑它的地方是 `bt check` 自身与提交流程）。
    """

    @staticmethod
    def _spy(monkeypatch) -> list[list[str]]:
        from btf.app import services

        calls: list[list[str]] = []

        def fake_call(cmd):
            calls.append(list(cmd))
            return 0

        monkeypatch.setattr(services.subprocess, "call", fake_call)
        return calls

    def test_check_forwards_to_tools_check(self, monkeypatch):
        calls = self._spy(monkeypatch)
        assert main(["check"]) == 0
        assert len(calls) == 1
        assert calls[0][0] == sys.executable
        assert calls[0][1] == str(ROOT / "tools" / "check.py")
        assert Path(calls[0][1]).is_file()      # 单一真源确实存在

    def test_check_passes_exit_code_through(self, monkeypatch):
        from btf.app import services

        monkeypatch.setattr(services.subprocess, "call", lambda cmd: 1)
        assert main(["check"]) == 1

    def test_cold_backup_forwards_reason_and_out(self, monkeypatch):
        calls = self._spy(monkeypatch)
        assert main(["cold-backup", "--reason", "收编留痕",
                     "--out", "X:/backup"]) == 0
        assert calls[0][1:] == [str(ROOT / "tools" / "cold_backup.py"),
                                "--reason", "收编留痕", "--out", "X:/backup"]

    def test_cold_backup_verify_excludes_new_backup(self, monkeypatch):
        """`--verify` 与新建互斥：只转发 --verify（不传 --reason/--out）。"""
        calls = self._spy(monkeypatch)
        assert main(["cold-backup", "--verify", "X:/old",
                     "--reason", "应被忽略"]) == 0
        assert calls[0][1:] == [str(ROOT / "tools" / "cold_backup.py"),
                                "--verify", "X:/old"]


class TestRunsAndShowCommands:
    """P2：产物只读查询（列 run / 看 manifest+指标）——不触发重算。"""

    @staticmethod
    def _make_run(config_path, out_dir) -> str:
        assert main(["run", "--config", str(config_path),
                     "--out", str(out_dir)]) == 0
        return sorted(p.name for p in out_dir.iterdir() if p.is_dir())[-1]

    def test_runs_lists_run_id_status_metrics(self, config_path, tmp_path, capsys):
        out_dir = tmp_path / "runs"
        run_id = self._make_run(config_path, out_dir)
        capsys.readouterr()                      # 丢弃 bt run 的输出
        assert main(["runs", "--out-dir", str(out_dir)]) == 0
        text = capsys.readouterr().out
        assert run_id in text and "COMPLETED" in text
        assert "total_return=" in text and "n_fills=" in text

    def test_runs_latest_limits_rows(self, config_path, tmp_path, capsys):
        out_dir = tmp_path / "runs"
        self._make_run(config_path, out_dir)
        second = self._make_run(config_path, out_dir)
        capsys.readouterr()
        assert main(["runs", "--out-dir", str(out_dir), "--latest", "1"]) == 0
        rows = [ln for ln in capsys.readouterr().out.splitlines()
                if "COMPLETED" in ln]
        assert len(rows) == 1 and second in rows[0]

    def test_runs_empty_dir_is_ok(self, tmp_path, capsys):
        assert main(["runs", "--out-dir", str(tmp_path / "none")]) == 0
        assert "无 run 产物" in capsys.readouterr().out

    def test_runs_corrupt_manifest_is_visible(self, tmp_path, capsys):
        """坏 manifest 不静默：行照列 + [warn] 写明原因（fail-visible）。"""
        out_dir = tmp_path / "runs"
        bad = out_dir / "20260101_000000_deadbeef"
        bad.mkdir(parents=True)
        (bad / "manifest.json").write_text("{ 不是 JSON", encoding="utf-8")
        assert main(["runs", "--out-dir", str(out_dir)]) == 0
        text = capsys.readouterr().out
        assert "20260101_000000_deadbeef" in text and "[warn]" in text

    def test_show_prints_manifest_and_metrics(self, config_path, tmp_path, capsys):
        out_dir = tmp_path / "runs"
        run_id = self._make_run(config_path, out_dir)
        capsys.readouterr()
        assert main(["show", "--run", run_id, "--out-dir", str(out_dir)]) == 0
        text = capsys.readouterr().out
        assert run_id in text and "COMPLETED" in text
        assert "metrics_digest" in text and "sharpe_ratio" in text

    def test_show_unknown_run_exits_one(self, tmp_path, capsys):
        assert main(["show", "--run", "nope",
                     "--out-dir", str(tmp_path / "runs")]) == 1
        assert "[error]" in capsys.readouterr().err

    def test_show_failed_run_warns_but_exits_zero(self, config_path, tmp_path,
                                                  capsys):
        """FAILED run 仍可看（审计价值），仅 stderr 告警——不伪装成"查不到"。"""
        out_dir = tmp_path / "runs"
        run_id = self._make_run(config_path, out_dir)
        manifest_path = out_dir / run_id / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["status"] = "FAILED"
        payload["error"] = "注入：测试用"
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False),
                                 encoding="utf-8")
        capsys.readouterr()
        assert main(["show", "--run", run_id, "--out-dir", str(out_dir)]) == 0
        captured = capsys.readouterr()
        assert "FAILED" in captured.out and "[warn]" in captured.err
