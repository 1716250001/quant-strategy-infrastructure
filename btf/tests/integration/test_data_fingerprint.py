# -*- coding: utf-8 -*-
"""L4 集成：**数据指纹落真值**（BB-1 重路；19 号 §33.6① P2-NEW-7 / §39.4 / §34.3）。

背景（空洞披露，铁律新 16）：`manifest.data_version` 原恒为
`{anchor_date: null, content_hash: "sha256:unknown", tables: []}`——接口留了、
读口接了、**写口从未发生**（`data/version.py::compute()` 零生产调用）。
后果：报告「数据指纹」栏永远渲染 `sha256:unknown`；**数据一更新，"可复现"即
失去锚点**（无法判定结果变化来自"数据变"还是"代码变"）。

老大裁决（§39.4）：**直接走重路**——`run` 启动时调用 `compute()` **实算**指纹
并注入 manifest（轻路"自己声明"不构成证明，不作过渡）。

本文件钉住四条验收（§34.3 BB-1）：
    ① 真入口 `bt run` 后 `data_version` **取到非占位值**（content_hash ≠ unknown /
       tables 非空 / anchor_date 非 null）；
    ② 报告「数据指纹」栏**渲染真实值**（渲染级断言，铁律新 14）；
    ③ **变异性检查**（铁律新 15）：数据变 → 指纹必变；
    ④ 性能留痕：装配期指纹耗时被记录（不得显著拖慢启动）。
"""
from __future__ import annotations

import json
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
        # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
        "risk": {"rules": [], "allow_empty_chain": True},
    }
    path = tmp_path / "fp.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


@requires_mainlib
class TestDataFingerprintEndToEnd:
    def test_cli_run_writes_real_data_version(self, tmp_path):
        """① 真入口 run → data_version 全字段真值；② 报告渲染真实指纹。"""
        from btf.cli.main import main

        cfg = _write_config(tmp_path)
        out_dir = tmp_path / "runs"
        assert main(["run", "--config", str(cfg), "--out", str(out_dir)]) == 0
        run_dirs = [p for p in out_dir.iterdir() if p.is_dir()]
        assert len(run_dirs) == 1
        run_id = run_dirs[0].name

        manifest = json.loads((out_dir / run_id / "manifest.json").read_text(
            encoding="utf-8"))
        dv = manifest["data_version"]
        assert dv["content_hash"].startswith("sha256:")
        assert dv["content_hash"] != "sha256:unknown", (
            "数据指纹仍为占位——空洞披露回归（P2-NEW-7 / 铁律新 16）")
        assert dv["tables"], "tables 为空（指纹未覆盖任何表）"
        assert dv["anchor_date"], "anchor_date 为 null（数据水位未落）"
        assert dv["anchor_date"].startswith("2024"), dv["anchor_date"]
        # 明细可追溯（逐表行数/日期范围/抽样哈希）
        assert "daily" in dv.get("tables_detail", {})
        assert dv["tables_detail"]["daily"]["row_count"] > 0

        # ② 报告渲染级断言（铁律新 14）：指纹真值可见
        assert main(["report", "--run", run_id, "--out-dir", str(out_dir)]) == 0
        html = (out_dir / run_id / "report.html").read_text(encoding="utf-8")
        assert dv["content_hash"] in html, "报告未渲染真实数据指纹"
        assert "sha256:unknown" not in html, "报告仍渲染占位指纹"

    def test_fingerprint_only_on_persist_path(self, tmp_path):
        """④ **零成本不变量**：`build()` 不算指纹；只在**落盘路径**算且毫秒级。

        为什么必须如此（§40.5 D-7 回归）：首版把指纹放在 `build()` → 网格搜索
        （每组合 build + persist=False）**每组合各付一次** 7.2s（10 年 6 表）→
        B4 外推 664s → **3647s**（超 1800s 预算）。现只在 `run(persist=True)`
        计算（披露字段只在有产物时有意义），且缺省 `meta` 档（元数据）。
        """
        from btf.experiment.store import LocalRunStore
        from btf.runtime import BTFRuntime

        rt = BTFRuntime().load_config(_write_config(tmp_path)).build()
        assert rt.data_version_info == {}, "build() 不应计算指纹（零成本不变量）"
        assert rt.data_fingerprint_seconds == 0.0

        rt.run(store=LocalRunStore(tmp_path / "runs"))
        assert rt.data_fingerprint_seconds > 0, "落盘路径未计算指纹"
        print(f"\n指纹耗时（缺省 meta 档）："
              f"{rt.data_fingerprint_seconds:.3f}s")
        assert rt.data_fingerprint_seconds < 5.0, (
            f"缺省档指纹耗时 {rt.data_fingerprint_seconds:.1f}s 过高"
            f"（meta 档应毫秒级）")
        assert rt.data_version_info["mode"] == "meta"

    def test_grid_path_pays_nothing(self, tmp_path):
        """④b 网格路径不变量：`build()` + `run(persist=False)` 后仍未算指纹。"""
        from btf.runtime import BTFRuntime

        rt = BTFRuntime().load_config(_write_config(tmp_path)).build()
        rt.run(persist=False)
        assert rt.data_fingerprint_seconds == 0.0, (
            "不落盘路径不得计算指纹（B4 网格回归守卫）")


class TestFingerprintVariability:
    """③ **变异性检查**（铁律新 15）：数据变 → 指纹必变（能触发差异的 fixture）。"""

    def _write_daily(self, root: Path, year: int, closes: list[float]) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        (root / "daily").mkdir(parents=True, exist_ok=True)
        table = pa.table({
            "ts_code": ["000001.SZ"] * len(closes),
            "trade_date": [f"{year}010{i + 1:02d}" for i in range(len(closes))],
            "close": closes,
        })
        pq.write_table(table, root / "daily" / f"{year}.parquet")

    def test_fast_digest_changes_when_key_columns_change(self, tmp_path):
        """fast 档：**键列**（trade_date/ts_code）变更 → 指纹必变。"""
        from btf.data.version import compute

        root = tmp_path / "market"
        self._write_daily(root, 2015, [10.0, 10.5, 11.0])
        before = compute(["daily"], root, mode="fast", years=[2015]).digest

        # 结构性变更：多一行 / 日期位移（fast 档的检测面）
        self._write_daily(root, 2015, [10.0, 10.5, 11.0, 11.7])
        after = compute(["daily"], root, mode="fast", years=[2015]).digest
        assert before != after, "键列变更未反映到指纹（伪防线：指纹恒同）"
        assert before.startswith("sha256:") and after.startswith("sha256:")

    def test_meta_digest_changes_on_rewrite(self, tmp_path):
        """**缺省档（meta）**也须可证伪：数据重写 → 指纹变（mtime/大小/统计）。"""
        from btf.data.version import compute

        root = tmp_path / "market"
        self._write_daily(root, 2015, [10.0, 10.5, 11.0])
        before = compute(["daily"], root, mode="meta", years=[2015]).digest
        self._write_daily(root, 2015, [10.0, 10.5, 11.7])
        after = compute(["daily"], root, mode="meta", years=[2015]).digest
        assert before != after, "meta 档未检出数据重写（伪防线）"

    def test_full_digest_catches_value_revision(self, tmp_path):
        """full 档**可检出数值级修订**；fast 档不可——档位语义须显式（§39.4）。

        这条差异是**设计而非缺陷**（`data/version.py` 模块 docstring：full 为
        "数据修正检测用"）；本测试把差异钉成断言，避免后来者误以为 fast 能
        替代 full。
        """
        from btf.data.version import compute

        root = tmp_path / "market"
        self._write_daily(root, 2015, [10.0, 10.5, 11.0])
        fast_before = compute(["daily"], root, mode="fast", years=[2015]).digest
        full_before = compute(["daily"], root, mode="full", years=[2015]).digest

        self._write_daily(root, 2015, [10.0, 10.5, 11.7])      # 仅改数值
        fast_after = compute(["daily"], root, mode="fast", years=[2015]).digest
        full_after = compute(["daily"], root, mode="full", years=[2015]).digest

        assert full_before != full_after, "full 档未检出数值级修订（伪防线）"
        assert fast_before == fast_after, (
            "fast 档只指纹键列——若此处变了，说明抽样面被改动（实现漂移）")

    def test_years_filter_scopes_fingerprint(self, tmp_path):
        """区间年过滤（BB-1 性能要点）：不同 years → 覆盖不同文件 → 指纹不同。"""
        from btf.data.version import compute

        root = tmp_path / "market"
        self._write_daily(root, 2015, [10.0, 11.0])
        self._write_daily(root, 2016, [20.0, 21.0])
        only_2015 = compute(["daily"], root, mode="fast", years=[2015])
        both = compute(["daily"], root, mode="fast", years=[2015, 2016])
        assert only_2015.tables["daily"].row_count == 2
        assert both.tables["daily"].row_count == 4
        assert only_2015.digest != both.digest

    def test_missing_files_yield_zero_rows_not_crash(self, tmp_path):
        """表缺失（未落盘/区间外）→ 零行指纹（不崩、不静默回落 unknown）。"""
        from btf.data.version import compute

        fp = compute(["daily", "stk_limit"], tmp_path / "empty",
                     mode="fast", years=[2015])
        assert fp.tables["daily"].row_count == 0
        assert fp.digest.startswith("sha256:")     # 仍是**真值**（空是事实）
