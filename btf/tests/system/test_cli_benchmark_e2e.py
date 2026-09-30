# -*- coding: utf-8 -*-
"""L5 系统：**CLI 真入口**带基准端到端（P0-NEW-7 / §24.4 第五种逃逸模式根治）。

为什么本文件存在（19 号 §24.3/§24.4）：

- 缺陷：配置 `report.benchmark` 后 **CLI run 必崩**——`relative_metrics` 的
  `benchmark_symbol`（字符串）混入数值指标集 → `store.save_metrics` 的
  `float(v)` 在**回测跑完之后**抛 `ValueError`（长区间白烧十几分钟）。
- 逃逸原因：既有系统测试（`test_v77_full_range.py`）用 `rt.run(persist=False)`
  + 直接读内存 dict 断言 → **落盘路径无人覆盖**。这是继「同错互证 / 伪造输入 /
  机检只比数值 / 端到端不看分布」之后的第五种逃逸：**测试与生产执行路径不一致**。
- 处置（§24.7 第 3 条，铁律新 13）：系统级端到端**必须经 CLI 真入口**
  （`run` → 落盘 → `report` → `verify`），禁以 `persist=False` 充当系统级验收。

本测试同时钉住三处修复：
    ① run 不再崩（P0-NEW-7）+ metrics.json **纯数值**（元信息已剥离）；
    ② `bt verify` 摘要一致（复核补算基准项——原复核只算基础 15 项 → 配基准
      的 run 摘要必然不符，假告警）；
    ③ 报告披露区含基准与宇宙口径（P1-NEW-5 / P1-NEW-6）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from btf import registry
from btf.config.paths import MARKET_DATA_DIR
from btf.domain.contracts import CONTRACT_VERSION

pytestmark = [pytest.mark.l5]

FEED_NAME = "cli_bench_e2e_feed"
ROOT = Path(MARKET_DATA_DIR)
BENCHMARK = "000300.SH"
requires_mainlib = pytest.mark.skipif(
    not (ROOT / "index_daily").is_dir(), reason=f"主库不可用: {ROOT}")


@pytest.fixture()
def _feed_registered():
    from tests.fixtures.scenarios import t_plus1_scenario

    feed = t_plus1_scenario().feed

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, _factory)
    return feed


def _write_config(tmp_path: Path, *, benchmark: str = BENCHMARK) -> Path:
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
        # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
        "risk": {"rules": [], "allow_empty_chain": True},
        "report": {"benchmark": benchmark},
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


@requires_mainlib
class TestCliBenchmarkEndToEnd:
    def test_run_persist_report_verify_all_green(self, _feed_registered,
                                                tmp_path):
        """真入口四步：run（落盘）→ metrics 纯数值 → report → verify 一致。"""
        from btf.cli.main import main

        cfg = _write_config(tmp_path)
        out_dir = tmp_path / "runs"

        # ① run（**落盘路径**——原实现此处必崩：float('000300.SH')）
        assert main(["run", "--config", str(cfg),
                     "--out", str(out_dir)]) == 0
        run_dirs = [p for p in out_dir.iterdir() if p.is_dir()]
        assert len(run_dirs) == 1, f"run 目录异常：{run_dirs}"
        run_id = run_dirs[0].name

        # ② metrics.json 纯数值 + 含基准项（P0-NEW-7：元信息已剥离）
        payload = json.loads((out_dir / run_id / "metrics.json").read_text(
            encoding="utf-8"))
        metrics = payload["metrics"]
        assert "benchmark_symbol" not in metrics, (
            "benchmark_symbol 是元信息，混入数值指标集会崩 save_metrics")
        for key in ("benchmark_total_return", "excess_return",
                    "tracking_error", "information_ratio"):
            assert isinstance(metrics[key], (int, float)), key
        assert metrics["benchmark_total_return"] != 0.0

        # ③ 报告：基准与宇宙口径进披露区（P1-NEW-5 / P1-NEW-6）
        #    + **渲染级断言**（Z-1/Z-2，铁律新 14）：报告是唯一人读交付物，
        #    正文片段被 autoescape 整体转义时"标签失形、文本完好"——文本级
        #    断言结构性失明（第六种逃逸模式），故须落到渲染后结构。
        assert main(["report", "--run", run_id, "--out-dir", str(out_dir)]) == 0
        html = (out_dir / run_id / "report.html").read_text(encoding="utf-8")
        assert BENCHMARK in html, "报告未披露基准（口径不可追溯）"
        assert "<div class='cards'>" in html, "指标卡未成形（正文被转义？）"
        assert html.count("<script") >= 1, "图表脚本被转义 → 六图不渲染"
        assert "<table><thead>" in html, "表格未成形（正文被转义？）"
        assert "&lt;div class=" not in html, "正文仍被整体转义（Z-1 回归）"

        # ④ verify：复核摘要与 manifest 一致（复核须补算基准项，否则假告警）
        assert main(["verify", "--run", run_id, "--out-dir", str(out_dir)]) == 0
        manifest = json.loads((out_dir / run_id / "manifest.json").read_text(
            encoding="utf-8"))
        assert manifest["status"] == "COMPLETED"
        assert manifest["metrics_digest"]
        assert "report" in manifest["config_effective"]

    def test_bad_benchmark_format_rejected_at_config_check(self, _feed_registered,
                                                          tmp_path):
        """P1-NEW-6：形制非法 → `bt config-check` 即拒（不必跑回测）。"""
        from btf.cli.main import main

        cfg = _write_config(tmp_path, benchmark="000300")   # 缺交易所后缀
        assert main(["config-check", str(cfg)]) == 1

    def test_benchmark_without_data_rejected_at_assembly(self, _feed_registered,
                                                        tmp_path):
        """P1-NEW-6：区间内零行情 → **装配期** ConfigError（不白跑整段回测）。"""
        from btf.runtime import BTFRuntime

        # 形制合法但主库无该指数（拼错交易所/代码）→ 装配期即拒
        cfg = _write_config(tmp_path, benchmark="999999.SH")
        rt = BTFRuntime().load_config(str(cfg))
        with pytest.raises(Exception, match=r"无任何行情|index_daily 零行"):
            rt.build()
