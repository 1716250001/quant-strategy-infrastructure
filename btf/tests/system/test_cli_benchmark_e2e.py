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


def _write_config(tmp_path: Path, *,
                  benchmark: str | list[str] = BENCHMARK) -> Path:
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

    def test_optimize_grid_includes_information_ratio(self, _feed_registered,
                                                      tmp_path):
        """RC-08（2026-10-05）：配 `report.benchmark` ⇒ **网格组合**含基准
        相对项（IR / 超额），可作 `--objective information_ratio` 排序键
        （与 `bt run` 同口径；未配基准不报错见 L1 用例）。"""
        from btf.cli.main import main

        cfg = _write_config(tmp_path)
        out_json = tmp_path / "grid.json"
        assert main(["optimize", "--config", str(cfg),
                     "--grid", "run.params.weight=0.9",
                     "--objective", "information_ratio",
                     "--workers", "0", "--allow-short",
                     "--out", str(out_json)]) == 0
        report = json.loads(out_json.read_text(encoding="utf-8"))
        metrics = report["results"][0]["metrics"]
        for key in ("information_ratio", "annualized_excess",
                    "tracking_error", "excess_return"):
            assert key in metrics, key

    def test_run_with_benchmark_no_no_benchmark_warn(self, _feed_registered,
                                                     tmp_path, capsys):
        """RC-09 反向探针：配了 benchmark ⇒ 终端**不得**出现「无基准对照」
        （防披露"恒现"——与无基准用例构成双向对照）。"""
        from btf.cli.main import main

        cfg = _write_config(tmp_path)
        assert main(["run", "--config", str(cfg),
                     "--out", str(tmp_path / "runs")]) == 0
        out = capsys.readouterr().out
        assert "无基准对照" not in out

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


#: 第二基准（上证指数；主库 index_daily 同区间有行情——已实测 5 日）
SECOND_BENCHMARK = "000001.SH"

#: 相对指标键（相对指标集全量；主基准独占 metrics 的断言对象）
_RELATIVE_KEYS = ("benchmark_total_return", "excess_return",
                  "annualized_excess", "tracking_error", "information_ratio",
                  "beta", "alpha_annualized", "up_capture", "down_capture")


def _same(left: float, right: float) -> bool:
    """浮点逐位等价（NaN＝NaN 视为相同——capture 类指标可能 NaN）。"""
    if left != left or right != right:
        return left != left and right != right
    return left == right


@requires_mainlib
class TestCliMultiBenchmark:
    """MM-6（19 号 §137 B）：**CLI 真入口**双基准同跑（铁律新 13）。

    派单验收 ①②③④：
        ① 单基准配置仍单值通过（见 `_write_config` 默认参数与上方既有用例）；
        ② 双基准一次 run 同时产出两套相对指标，且**主基准数值 == 单基准时
           的数值（逐位）**，metrics 键集**不多不少**（第二基准不得污染）；
        ③ 报告渲染多基准对照表；④ `verify` 摘要一致（R1 digest 口径不变）。
    """

    def _run_once(self, main, cfg: Path, out_dir: Path) -> str:
        assert main(["run", "--config", str(cfg),
                     "--out", str(out_dir)]) == 0
        dirs = [p for p in out_dir.iterdir() if p.is_dir()]
        assert len(dirs) == 1, f"run 目录异常：{dirs}"
        return dirs[0].name

    def test_multi_benchmark_primary_metrics_equal_single(self, _feed_registered,
                                                          tmp_path):
        from btf.cli.main import main

        single_dir, multi_dir = tmp_path / "s", tmp_path / "m"
        single_dir.mkdir()
        multi_dir.mkdir()
        run_single = self._run_once(
            main, _write_config(single_dir, benchmark=BENCHMARK),
            single_dir / "runs")
        run_multi = self._run_once(
            main,
            _write_config(multi_dir,
                          benchmark=[BENCHMARK, SECOND_BENCHMARK]),
            multi_dir / "runs")

        m1 = json.loads((single_dir / "runs" / run_single / "metrics.json")
                        .read_text(encoding="utf-8"))["metrics"]
        m2 = json.loads((multi_dir / "runs" / run_multi / "metrics.json")
                        .read_text(encoding="utf-8"))["metrics"]

        # ② 键集不多不少 + 主基准数值逐位相同（加第二个基准**不改**任何数值）
        assert set(m2) == set(m1), "双基准改动了 metrics 键集（第二基准污染）"
        for key in _RELATIVE_KEYS:
            assert key in m2, key
            assert _same(m1[key], m2[key]), (
                f"{key}: 单基准 {m1[key]!r} ≠ 双基准主基准 {m2[key]!r}"
                f"（R1 digest 口径被破坏）")
        # P0-NEW-7：metrics 只许数值——第二基准代码/元信息不得混入
        assert SECOND_BENCHMARK not in json.dumps(m2), "第二基准代码混入 metrics"

        # ③ 披露：manifest.benchmarks 两条（首个＝主基准）
        mf = json.loads((multi_dir / "runs" / run_multi / "manifest.json")
                        .read_text(encoding="utf-8"))
        rows = mf["benchmarks"]
        assert [r["symbol"] for r in rows] == [BENCHMARK, SECOND_BENCHMARK]
        assert [r["primary"] for r in rows] == [True, False]
        for row in rows:
            for key in _RELATIVE_KEYS:
                assert key in row, f"披露明细缺 {key}（每个基准须一套）"
        # 主基准披露数值与 metrics 同源（同一计算，不得两套口径）
        for key in _RELATIVE_KEYS:
            assert _same(rows[0][key], m2[key]), key

        # ③ 渲染级：多基准对照表**成形**（标签级断言，Z-1/铁律新 14）
        assert main(["report", "--run", run_multi,
                     "--out-dir", str(multi_dir / "runs")]) == 0
        html = (multi_dir / "runs" / run_multi / "report.html").read_text(
            encoding="utf-8")
        assert "基准对照（多基准）" in html, "多基准对照章节缺失"
        assert BENCHMARK in html and SECOND_BENCHMARK in html
        assert "<table><thead>" in html, "对照表未成形（正文被转义？）"
        assert "&lt;div class=" not in html

        # ④ verify：落盘口径未变 ⇒ 复核摘要仍一致
        for out_dir, run_id in ((single_dir / "runs", run_single),
                                (multi_dir / "runs", run_multi)):
            assert main(["verify", "--run", run_id,
                         "--out-dir", str(out_dir)]) == 0, (
                f"{run_id} 复核不一致（多基准污染了 R1 digest？）")

    def test_second_benchmark_without_data_rejected_at_assembly(
            self, _feed_registered, tmp_path):
        """MM-6：次基准同样过装配期守卫（拼错即拒，不跑完整段才崩）。"""
        from btf.runtime import BTFRuntime

        cfg = _write_config(tmp_path,
                            benchmark=[BENCHMARK, "999999.SH"])
        rt = BTFRuntime().load_config(str(cfg))
        with pytest.raises(Exception, match=r"无任何行情|index_daily 零行"):
            rt.build()

