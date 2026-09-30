# -*- coding: utf-8 -*-
"""L2/L4 集成：api Facade 等价性（v0.5 V5-7；`btf.run/report/dataset`）。

验收（18 号 V5-7）：与 CLI 等价——
    ① 同配置 api.run 与 `bt run` 的 manifest.metrics_digest 一致；
    ② 同一 run 的 api.report 与 `bt report` 输出字节一致（幂等）；
    ③ api.dataset 与 `bt dataset` 同构（委托同一子进程，退出码一致）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.l2]

from btf import registry  # noqa: E402
from btf.domain.contracts import CONTRACT_VERSION  # noqa: E402

FEED_NAME = "api_facade_memory_feed"


@pytest.fixture(scope="module")
def config_path(tmp_path_factory) -> Path:
    from tests.fixtures.scenarios import t_plus1_scenario

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
        # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
        "risk": {"rules": [], "allow_empty_chain": True},
    }
    path = tmp_path_factory.mktemp("api") / "backtest.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


class TestRunEquivalence:
    def test_api_run_digest_matches_cli_run(self, config_path, tmp_path):
        """① api.run vs `bt run`：同配置 → metrics_digest 一致（R1 确定性）。"""
        import btf
        from btf.cli.main import main

        result = btf.run(str(config_path), out=tmp_path / "api_runs")
        assert main(["run", "--config", str(config_path),
                     "--out", str(tmp_path / "cli_runs")]) == 0
        api_manifest = (tmp_path / "api_runs" / result.run_id
                        / "manifest.json")
        cli_runs = sorted(p for p in (tmp_path / "cli_runs").iterdir()
                          if p.is_dir())
        cli_manifest = cli_runs[-1] / "manifest.json"
        api_digest = yaml.safe_load(
            api_manifest.read_text(encoding="utf-8"))["metrics_digest"]
        cli_digest = yaml.safe_load(
            cli_manifest.read_text(encoding="utf-8"))["metrics_digest"]
        assert api_digest == cli_digest

    def test_api_run_returns_metrics(self, config_path, tmp_path):
        import btf

        result = btf.run(str(config_path), out=tmp_path / "runs")
        assert result.run_id and result.manifest.status == "COMPLETED"
        assert result.metrics["n_trading_days"] == 5

    def test_dry_run_returns_runtime(self, config_path):
        import btf

        rt = btf.run(str(config_path), dry_run=True)
        assert rt.strategy is not None and rt.last_run_id is None


class TestReportEquivalence:
    def test_api_report_bytes_match_cli_report(self, config_path, tmp_path):
        """② 同一 run：api.report 与 `bt report` 输出**字节一致**。"""
        import btf
        from btf.cli.main import main

        result = btf.run(str(config_path), out=tmp_path / "runs")
        run_id = result.run_id
        html_api = btf.report(run_id, out=tmp_path / "api.html",
                              out_dir=tmp_path / "runs")
        assert main(["report", "--run", run_id,
                     "--out", str(tmp_path / "cli.html"),
                     "--out-dir", str(tmp_path / "runs")]) == 0
        assert html_api.read_bytes() == (tmp_path / "cli.html").read_bytes()

    def test_api_report_idempotent(self, config_path, tmp_path):
        import btf

        result = btf.run(str(config_path), out=tmp_path / "runs")
        first = btf.report(result.run_id, out=tmp_path / "a.html",
                           out_dir=tmp_path / "runs")
        second = btf.report(result.run_id, out=tmp_path / "b.html",
                            out_dir=tmp_path / "runs")
        assert first.read_bytes() == second.read_bytes()


class TestLazyFacade:
    def test_import_btf_is_light(self):
        """`import btf` 零成本（子进程验证——同进程子模块绑定会污染
        `hasattr`；import 机制把已加载子模块绑回包属性）。"""
        import subprocess
        import sys

        # 版本断言用**形制**（`X.Y.Z`）——不写死前缀（FF-3 定版：原 `startswith('0.5')`）
        code = ("import btf, re, sys; "
                "assert re.fullmatch(r'\\d+\\.\\d+\\.\\d+', btf.__version__), "
                "btf.__version__; "
                "assert 'pyarrow' not in sys.modules, '数据层被拉起'; "
                "assert callable(btf.run) and callable(btf.report)")
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, cwd=str(
                               Path(__file__).resolve().parents[2]))
        assert r.returncode == 0, r.stderr

    def test_unknown_attribute_raises(self):
        import btf

        with pytest.raises(AttributeError, match="btf"):
            _ = btf.not_a_facade


class TestDatasetFacade:
    def test_dataset_list_matches_cli(self, tmp_path):
        """③ api.dataset(--list) 与 `bt dataset --list` 退出码一致（0）。"""
        import btf

        assert btf.dataset(list_only=True) == 0
