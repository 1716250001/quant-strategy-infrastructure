# -*- coding: utf-8 -*-
"""R2203 G2：btf 可用性（check --full / layer choices / runs --json / --latest）。"""

from __future__ import annotations

import argparse
import json

import pytest
from btf.app import RunSummary
from btf.cli.main import _resolve_run_id, main


def test_layer_choices_reject_unknown():
    with pytest.raises(SystemExit) as exc:
        main(["test", "--layer", "l6"])
    assert exc.value.code == 2


def test_check_default_is_not_full(monkeypatch):
    seen = {}
    monkeypatch.setattr("btf.app.run_gate_check", lambda **kw: seen.update(kw) or 0)
    assert main(["check"]) == 0
    assert seen["full"] is False


def test_check_full_passes_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr("btf.app.run_gate_check", lambda **kw: seen.update(kw) or 0)
    assert main(["check", "--full"]) == 0
    assert seen["full"] is True


def test_runs_json(monkeypatch, capsys):
    rows = [
        RunSummary(
            run_id="r1",
            status="ok",
            created="2026-11-23 00:00",
            metrics={"sharpe_ratio": 1.2, "n_fills": 3},
            note="",
        )
    ]
    monkeypatch.setattr("btf.app.list_runs", lambda **kwargs: rows)
    assert main(["runs", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["run_id"] == "r1"
    assert payload[0]["metrics"]["sharpe_ratio"] == 1.2


def test_runs_json_empty_is_array(monkeypatch, capsys):
    monkeypatch.setattr("btf.app.list_runs", lambda **kwargs: [])
    assert main(["runs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_resolve_latest(monkeypatch):
    monkeypatch.setattr(
        "btf.app.list_runs",
        lambda **kwargs: [
            RunSummary("r-latest", "ok", "2026-11-23 00:00", {}, ""),
        ],
    )
    args = argparse.Namespace(run=None, out_dir=None)
    assert _resolve_run_id(args) == "r-latest"


def test_resolve_latest_empty(monkeypatch, capsys):
    monkeypatch.setattr("btf.app.list_runs", lambda **kwargs: [])
    args = argparse.Namespace(run=None, out_dir=None)
    assert _resolve_run_id(args) is None
    assert "无 run 产物" in capsys.readouterr().err


def test_report_requires_run_or_latest():
    with pytest.raises(SystemExit) as exc:
        main(["report"])
    assert exc.value.code == 2
