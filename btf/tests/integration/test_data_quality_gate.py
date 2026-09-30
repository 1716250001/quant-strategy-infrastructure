# -*- coding: utf-8 -*-
"""批 8 集成：装配期数据不变量门（strict 硬失败 / 默认告警+产物+披露）。

覆盖三条设计要点（19 号 §39.2）：
    ① fail-closed 边界：`strict=true` → `DataQualityError`；缺省 → 告警不阻断，
       但**必须**留痕（产物 + 报告披露）——**两条路径都不静默**（铁律新 16）；
    ② 性能/披露：宇宙超限按字典序前缀抽样，`sampled_symbols` 与报告注记可见；
    ③ 产物（CC-6）：run 目录 `data_quality_report.json` 恒定落盘（含"为何没校验"）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.experiment.store import LocalRunStore
from btf.runtime import BTFRuntime, DataQualityError

DAYS = ["20240102", "20240103", "20240104", "20240105", "20240108",
        "20240109", "20240110", "20240111", "20240112", "20240115"]
SYM, SYM2 = "600000.SH", "300001.SZ"


def _build_master(root: Path, *, bad_vol: bool = False,
                  st_breach: bool = False) -> None:
    (root / "metadata").mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([
        {"exchange": "SSE", "cal_date": d, "is_open": 1,
         "pretrade_date": DAYS[max(0, i - 1)]}
        for i, d in enumerate(DAYS)]), root / "metadata" / "trade_cal.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": SYM, "name": "浦发银行", "list_date": "19991110",
         "delist_date": None},
        {"ts_code": SYM2, "name": "特锐德", "list_date": "20091030",
         "delist_date": None}]), root / "metadata" / "stock_basic.parquet")

    def rows(sym: str) -> list[dict]:
        # SYM 在 DAYS[0] **全日停牌** → 该日**不产出行**（主库实测口径）
        days = [d for d in DAYS if not (sym == SYM and d == DAYS[0])]
        out = [{"ts_code": sym, "trade_date": d, "open": 10.0, "high": 10.0,
                "low": 10.0, "close": 10.0, "pre_close": 10.0,
                "vol": 1000.0, "amount": 10_000.0} for d in days]
        if bad_vol and sym == SYM:
            out[0]["vol"] = -1.0
        if st_breach and sym == SYM:
            # ST 股（DAYS[2] 起在册）单日 **+8%**：> ST 5% 上限、< 主板 10%
            for row in out:
                if row["trade_date"] == DAYS[2]:
                    row["pre_close"] = round(row["close"] / 1.08, 6)
        return out

    for table in ("daily",):
        (root / table).mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows(SYM) + rows(SYM2)),
                       root / table / "2024.parquet")
    for table, cols in (
            ("stk_limit", {"up_limit": 11.0, "down_limit": 9.0}),
            ("adj_factor", {"adj_factor": 1.0})):
        (root / table).mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(
            [{"ts_code": s, "trade_date": d, **cols}
             for s in (SYM, SYM2) for d in DAYS]),
            root / table / "2024.parquet")
    (root / "suspend_d").mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": SYM, "trade_date": DAYS[0], "suspend_timing": None,
         "suspend_type": "S"}]), root / "suspend_d" / "2024.parquet")
    (root / "dividend").mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": SYM, "ex_date": "20240109", "pay_date": "20240109",
         "record_date": "20240108", "ann_date": "20231201",
         "div_proc": "实施", "stk_bo_rate": None, "stk_co_rate": None,
         "cash_div": 0.1, "end_date": "20231231"}]),
        root / "dividend" / "2024.parquet")
    if st_breach:
        # ST 在册区间（namechange）：SYM 自 DAYS[2] 起为 ST（无结束日）
        (root / "namechange").mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist([
            {"ts_code": SYM, "name": "*ST 浦发", "start_date": DAYS[2],
             "end_date": None, "ann_date": DAYS[1], "change_reason": "ST"}]),
            root / "namechange" / "2024.parquet")


def _config(root: Path, **quality) -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "btf.strategy.monthly:MonthlyEqualWeight",
            "params": {"symbols": [SYM, SYM2]},
            "universe": {"source": "explicit", "symbols": [SYM, SYM2]},
            "period": {"start": "2024-01-02", "end": "2024-01-15"},
            "initial_cash": 100_000,
        },
        "data": {"feed": "tushare_parquet", "feed_params": {"root": str(root)},
                 **({"quality": quality} if quality else {})},
        "execution": {"handler": "next_open", "cost_model": "flat_rate",
                      "rebalancer": "full"},
        "risk": {"rules": [], "allow_empty_chain": True},
    }


def test_default_records_and_discloses(tmp_path: Path):
    """缺省：装配期校验 → 结果入 `rt.data_quality` + 报告披露通道（assembly_notes）。"""
    root = tmp_path / "market"
    _build_master(root)
    rt = BTFRuntime().load_config(_config(root)).build()
    assert rt.data_quality["ok"] is True
    assert rt.data_quality["checked_symbols"] == 2
    assert any("数据不变量校验" in n for n in rt.assembly_notes), rt.assembly_notes
    # CC-6：产物随 run 落盘
    rt.run(store=LocalRunStore(tmp_path / "runs"))
    artifact = tmp_path / "runs" / rt.last_run_id / "data_quality_report.json"
    assert artifact.is_file(), "run 目录缺 data_quality_report.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert all(r["checked_rows"] > 0 for r in payload["rules"]), "空洞披露"


def test_strict_raises_on_findings(tmp_path: Path):
    """strict=true：发现异常 → 装配期 DataQualityError（fail-closed）。"""
    root = tmp_path / "market"
    _build_master(root, bad_vol=True)
    rt = BTFRuntime().load_config(_config(root, strict=True))
    with pytest.raises(DataQualityError, match=r"strict=true|fail-closed"):
        rt.build()


class TestCC2STBranch:
    """批 9（DD-1/DD-2）：CC-2 的 **ST 5% 上限**接线验证（P2-NEW-8）。

    此前 `runtime._run_data_quality` 调 `run_checks` **未传 `st_symbols`** ⇒
    ST 分支从未生效（恒按板块上限 10%）⇒ ST 股 5%~10% 的越界**漏检**。
    本组用「ST 股 +8%」这一**介于两档之间**的信号做双向断言：
      · 接线后 → **必须检出**（分支真在跑）；
      · 若为旧行为（不传 ST）→ **不检出**（漏检面，见单测对照）。
    """

    def test_st_branch_fires_in_real_run(self, tmp_path: Path):
        """真入口：ST 股 +8% → CC-2 检出；`st_source`/`st_pairs_count` 取真值。"""
        root = tmp_path / "market"
        _build_master(root, st_breach=True)
        rt = BTFRuntime().load_config(_config(root)).build()
        dq = rt.data_quality
        assert dq["st_source"] == "namechange", dq
        assert dq["st_pairs_count"] > 0, "ST 名单为空（未接线？铁律新 16）"
        cc2 = next(r for r in dq["rules"] if r["rule"] == "CC-2")
        assert not cc2["ok"], "ST 股 +8%（> 5% 上限）必须检出"
        assert cc2["n_findings"] >= 1
        # 披露不静默：装配说明须提"异常"（与默认路径一致）
        assert any("数据不变量校验" in n for n in rt.assembly_notes)

    def test_st_artifact_carries_st_fields(self, tmp_path: Path):
        """CC-6 产物：ST 字段随产物落盘且**非占位**（可核）。"""
        root = tmp_path / "market"
        _build_master(root, st_breach=True)
        rt = BTFRuntime().load_config(_config(root)).build()
        rt.run(store=LocalRunStore(tmp_path / "runs"))
        payload = json.loads(
            (tmp_path / "runs" / rt.last_run_id / "data_quality_report.json"
             ).read_text(encoding="utf-8"))
        assert payload["st_source"] == "namechange"
        assert payload["st_pairs_count"] > 0

    def test_clean_run_reports_st_source(self, tmp_path: Path):
        """无 ST 的合成库：`st_source` 仍须**有值**（不得为空/unknown）。"""
        root = tmp_path / "market"
        _build_master(root)
        rt = BTFRuntime().load_config(_config(root)).build()
        assert rt.data_quality["st_source"] in ("namechange", "unavailable")
        assert "st_pairs_count" in rt.data_quality


def test_disabled_is_disclosed_not_silent(tmp_path: Path):
    """enabled=false：**披露**"为什么没校验"（不得静默跳过）。"""
    root = tmp_path / "market"
    _build_master(root, bad_vol=True)          # 数据其实是脏的
    rt = BTFRuntime().load_config(_config(root, enabled=False)).build()
    assert rt.data_quality["enabled"] is False
    assert "配置关闭" in rt.data_quality["note"]


def test_sampling_is_disclosed(tmp_path: Path):
    """max_symbols：宇宙超限 → 决定性抽样且**必披露**（sampled + 报告注记）。"""
    root = tmp_path / "market"
    _build_master(root)
    rt = BTFRuntime().load_config(_config(root, max_symbols=1)).build()
    assert rt.data_quality["sampled_symbols"] is True
    assert rt.data_quality["checked_symbols"] == 1
    assert any("抽样" in n for n in rt.assembly_notes), rt.assembly_notes


def test_report_renders_quality_note(tmp_path: Path):
    """渲染级（新 14）：**真实产物**报告中可见「数据不变量校验」披露行。

    链路：`build()`（写入 assembly_notes）→ `run(persist=True)` →
    `store.read_bundle(run_id)` → `ReportBuilder.build()`（真渲染）——
    确保披露**到达交付物**而非停在内存（铁律新 13/14）。
    """
    from btf.viz.report import ReportBuilder

    root = tmp_path / "market"
    _build_master(root)
    store = LocalRunStore(tmp_path / "runs")
    rt = BTFRuntime().load_config(_config(root)).build()
    result = rt.run(store=store)
    bundle = store.load(result.run_id)
    html = ReportBuilder(bundle).build()
    assert "数据不变量校验" in html, "报告未渲染数据质量披露"
    assert 'id="assumptions"' in html
