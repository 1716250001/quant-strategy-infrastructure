# -*- coding: utf-8 -*-
"""T-18（19 号 §149）工具层缺陷回归：D-1 WF 门禁跳过三处披露 / D-2 仓外 lookback 注入。

D-1：`bt validate walk-forward` 折内**只跳哈希门禁**（`version_gate=
skipped_walk_forward`），`anchor_date` 水位门禁照旧；三处披露（manifest /
结果 JSON / 控制台）缺一即验收不通过——本文件锁前两处（控制台由
`_cmd_validate` 打印同一字段）。
D-2：`declared_lookback` 导入前先按 `run.strategy_paths` 注入 `sys.path`
（与 `Runtime._load_strategy` 共用 `inject_strategy_paths`），否则仓外策略
必 `ImportError` ⇒ `--warmup auto` 静默回落 252。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from types import SimpleNamespace

from btf.config.validation import ConfigError
from btf.domain.types import TradingDate
from btf.optimize import walkforward as wf
from btf.runtime import component_assembler as ca

DEMO_OK = '''\
class ProbeStrategy:
    lookback_bars = 2
'''

DEMO_NO_ATTR = '''\
class NoAttrStrategy:
    pass
'''


def _days(n: int = 30) -> list[TradingDate]:
    """连续 n 个工作日（TradingDate；足够切 5/5 折）。"""
    out: list[TradingDate] = []
    day = date(2024, 1, 1)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(TradingDate.from_ymd(day.strftime("%Y%m%d")))
        day += timedelta(days=1)
    return out


class _AsmStub:
    """`ComponentAssembler.guard_dataset_version` 的最小 self（只用到 3 个成员）。"""

    def __init__(self, *, skip: str | None, date_max: str = "20261231") -> None:
        self.rt = SimpleNamespace(
            config={"data": {"dataset": {"expected_hash": "sha256:declared"}}},
            assembly_notes=[],
            version_gate_skip_reason=skip,
            version_gate_skipped_hash=False,
        )
        self._detail = {"daily": {"date_max": date_max}}

    def compute_data_version(self, period):
        return {"mode": "fast", "content_hash": "sha256:actual",
                "tables_detail": self._detail}


class TestD1VersionGateSkip:
    def test_hash_mismatch_fails_closed_without_skip(self):
        """基线：不置跳过 ⇒ 哈希不一致仍**拒载**（不得顺手放宽 `bt run`）。"""
        asm = _AsmStub(skip=None)
        try:
            ca.ComponentAssembler.guard_dataset_version(asm, {})
        except ConfigError as exc:
            assert "RC-14" in str(exc)
        else:
            raise AssertionError("未声明跳过时哈希不一致必须 ConfigError")

    def test_skip_records_disclosure_and_keeps_anchor_gate(self):
        """置跳过 ⇒ 放行并落 `version_gate=` 披露；水位门禁**照旧生效**。"""
        asm = _AsmStub(skip="skipped_walk_forward")
        ca.ComponentAssembler.guard_dataset_version(asm, {})
        assert asm.rt.version_gate_skipped_hash is True
        joined = "；".join(asm.rt.assembly_notes)
        assert "version_gate=skipped_walk_forward" in joined
        assert "anchor_date 水位门禁仍生效" in joined

    def test_skip_does_not_weaken_anchor_gate(self):
        """跳过**只**针对哈希：水位不足仍 fail-closed（D-1 反向验收）。"""
        asm = _AsmStub(skip="skipped_walk_forward", date_max="20200101")
        asm.rt.config["data"]["dataset"]["anchor_date"] = "20261231"
        try:
            ca.ComponentAssembler.guard_dataset_version(asm, {})
        except ConfigError as exc:
            assert "数据水位" in str(exc)
        else:
            raise AssertionError("水位不足必须 ConfigError（跳过≠放宽水位）")

    def test_walk_forward_reports_version_gate(self, monkeypatch):
        """WF 每折置跳过标记，结果 JSON 与每折均披露 `skipped_walk_forward`。"""
        built: list[SimpleNamespace] = []

        class _FakeRT:
            def __init__(self) -> None:
                self.assembly_notes: list[str] = []
                self.version_gate_skip_reason: str | None = None
                self.version_gate_skipped_hash = False
                self.run_id = "fake-run"

            def load_config(self, cfg):
                self.config = cfg
                return self

            def build(self):
                if self.version_gate_skip_reason:
                    self.version_gate_skipped_hash = True
                built.append(self)
                return self

            def run(self, **kwargs):
                return SimpleNamespace()

        monkeypatch.setattr("btf.runtime.BTFRuntime", _FakeRT)
        monkeypatch.setattr(wf, "_metrics_of", lambda *a, **k: {})

        report = wf.run_walk_forward(
            {"run": {"strategy": "x:Y"}}, _days(), train=5, test=5, step=5,
            persist=False, include_is=False, warmup=0)

        assert report["version_gate"] == "skipped_walk_forward"
        assert report["version_gate_note"]
        assert report["n_folds"] >= 2
        assert all(f["version_gate"] == "skipped_walk_forward"
                   for f in report["folds"])
        assert all(rt.version_gate_skip_reason == "skipped_walk_forward"
                   for rt in built)


class TestD2LookbackInjection:
    def test_out_of_tree_strategy_declaration_resolved(self, tmp_path):
        """仓外策略（strategy_paths）⇒ `declared_lookback` 读到声明值 2（非 None）。"""
        (tmp_path / "probe_strat.py").write_text(DEMO_OK, encoding="utf-8")
        cfg = {"run": {"strategy": "probe_strat:ProbeStrategy",
                       "strategy_paths": [str(tmp_path)]}}
        try:
            assert wf.declared_lookback(cfg) == 2
            assert wf._lookback_probe(cfg) == (2, "strategy")
        finally:
            sys.modules.pop("probe_strat", None)
            sys.path[:] = [p for p in sys.path if p != str(tmp_path.resolve())]

    def test_import_failure_reason_is_distinguishable(self, tmp_path):
        """导入失败与"类无该属性"两种降级原因**文案可区分**（禁静默同因）。"""
        (tmp_path / "no_attr.py").write_text(DEMO_NO_ATTR, encoding="utf-8")
        cfg = {"run": {"strategy": "no_attr:NoAttrStrategy",
                       "strategy_paths": [str(tmp_path)]}}
        try:
            assert wf.declared_lookback(cfg) is None
            assert wf._lookback_probe(cfg)[1] == "类无该属性"
        finally:
            sys.modules.pop("no_attr", None)
            sys.path[:] = [p for p in sys.path if p != str(tmp_path.resolve())]

        missing = {"run": {"strategy": "definitely_absent_mod:X",
                           "strategy_paths": [str(tmp_path)]}}
        assert wf.declared_lookback(missing) is None
        assert wf._lookback_probe(missing)[1].startswith("导入失败")
