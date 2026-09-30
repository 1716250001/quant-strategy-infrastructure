# -*- coding: utf-8 -*-
"""L1 单测：插件基线**刷新留痕**（AA-3，19 号 §30.8.3 / §31.3）。

背景（P3-NEW-2 / P2-NEW-1 的具体代价实例）：本项目非 git 仓库，
「扩展工作流核心 diff 为零」的**唯一凭据**是 `tools/.plugin_baseline.json`
快照；若"重新定基"这一动作本身无留痕，事后**无法区分**「合法随之刷新」与
「用刷新掩盖漂移」→ 使该纪律在事后不可证。

本测试钉住两件事（按铁律新 15：留痕机制本身是防线，须可被证伪）：
    ① 历史文件存在且**含可解析记录**（含批 5 的事后补记 retro 行）；
    ② **变更路径真的写记录**：注入受控哈希（模拟核心文件改动）→ 历史追加
       一行、`changed_files` 精确、`reason` 落库。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.l1]

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"


@pytest.fixture()
def _boundary(monkeypatch, tmp_path):
    """隔离的边界模块（基线/历史指向 tmp_path；哈希可控）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_plugin_boundary_under_test", TOOLS / "check_plugin_boundary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "BASELINE", tmp_path / "baseline.json")
    monkeypatch.setattr(mod, "BASELINE_HISTORY", tmp_path / "history.jsonl")
    return mod


class TestLedgerMechanism:
    def test_history_file_has_parseable_records(self):
        """生产历史文件存在且每行可解析（含 retro 补记）。"""
        history = TOOLS / ".plugin_baseline_history.jsonl"
        assert history.is_file(), "AA-3：留痕文件缺失"
        entries = [json.loads(line) for line in
                   history.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert entries, "留痕文件为空"
        for entry in entries:
            assert entry["at"] and entry["kind"] in ("create", "update", "retro")

    def test_update_records_changed_files(self, _boundary, monkeypatch, tmp_path):
        """★ 变更路径：受控哈希变化 → 历史追加且 `changed_files` 精确。"""
        monkeypatch.setattr(_boundary, "_core_hashes",
                            lambda: {"btf/a.py": "h1", "btf/b.py": "h2"})
        _boundary.check_baseline(update=True, reason="首建")     # 建立基线
        monkeypatch.setattr(_boundary, "_core_hashes",
                            lambda: {"btf/a.py": "h1", "btf/b.py": "h2-changed"})
        problems, updated, changed, created = _boundary.check_baseline(
            update=True, reason="AA-3 受控变更")
        assert updated and not problems and not created
        assert list(changed) == ["btf/b.py"], changed
        _boundary._record_history(changed, "AA-3 受控变更", created)
        lines = (_boundary.BASELINE_HISTORY.read_text(
            encoding="utf-8").strip().splitlines())
        entry = json.loads(lines[-1])
        assert entry["changed_files"] == ["btf/b.py"]
        assert entry["reason"] == "AA-3 受控变更"
        assert entry["changed"]["btf/b.py"]["old"] == "h2"      # 旧→新可追溯
        assert entry["changed"]["btf/b.py"]["new"] == "h2-changed"

    def test_no_change_writes_no_history(self, _boundary, monkeypatch):
        """无变更路径：不写历史（防噪声），且返回 changed 为空。"""
        monkeypatch.setattr(_boundary, "_core_hashes", lambda: {"btf/a.py": "h1"})
        _boundary.check_baseline(update=True, reason="首建")
        problems, updated, changed, _created = _boundary.check_baseline(
            update=True, reason="无变更")
        assert updated and changed == {} and problems == []
        assert not _boundary.BASELINE_HISTORY.exists(), "无变更不应写历史"
