# -*- coding: utf-8 -*-
"""R2203 G1-2：freshness CLI 默认 strict（裁定 #2）。

库层 `tools.db.freshness.run(strict=False)` 默认不变；两个 CLI 入口默认 strict：
  - `db freshness`：红/黄 → 1，读不全 → 3；
  - 顶层 `freshness`：同口径；
  - `--no-strict` 恢复恒 0。
"""

from __future__ import annotations

import argparse

import pytest

import tools.db.freshness as fr
from cli.db_cmds import cmd_db_freshness, cmd_freshness
from cli.parser import build_parser


def _db_args(strict: bool) -> argparse.Namespace:
    return argparse.Namespace(only=None, json=True, strict=strict)


def test_db_freshness_defaults_strict(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(fr, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        cmd_db_freshness(_db_args(True))
    assert exc.value.code == 1
    assert captured["strict"] is True


def test_db_freshness_no_strict(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(fr, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        cmd_db_freshness(_db_args(False))
    assert exc.value.code == 0
    assert captured["strict"] is False


def _top_args(strict: bool) -> argparse.Namespace:
    return argparse.Namespace(tables=None, all=False, stale=None, json=True, strict=strict)


def test_top_freshness_strict_red(monkeypatch):
    monkeypatch.setattr(
        "tools.quicklook.run_freshness",
        lambda **kwargs: [{"verdict": "红"}, {"verdict": "正常"}],
    )
    fr._UNREADABLE.clear()
    with pytest.raises(SystemExit) as exc:
        cmd_freshness(_top_args(True))
    assert exc.value.code == 1


def test_top_freshness_strict_unreadable(monkeypatch):
    monkeypatch.setattr("tools.quicklook.run_freshness", lambda **kwargs: [{"verdict": "正常"}])
    fr._UNREADABLE.clear()
    fr._UNREADABLE.append("broken.parquet")
    with pytest.raises(SystemExit) as exc:
        cmd_freshness(_top_args(True))
    assert exc.value.code == 3
    fr._UNREADABLE.clear()


def test_top_freshness_no_strict(monkeypatch):
    monkeypatch.setattr("tools.quicklook.run_freshness", lambda **kwargs: [{"verdict": "红"}])
    fr._UNREADABLE.clear()
    assert cmd_freshness(_top_args(False)) is None


def test_parser_default_strict_and_no_strict():
    parser = build_parser()
    assert parser.parse_args(["db", "freshness"]).strict is True
    assert parser.parse_args(["db", "freshness", "--no-strict"]).strict is False
    assert parser.parse_args(["freshness"]).strict is True
    assert parser.parse_args(["freshness", "--no-strict"]).strict is False
