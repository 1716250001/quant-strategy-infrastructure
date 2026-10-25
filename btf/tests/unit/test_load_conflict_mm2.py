# -*- coding: utf-8 -*-
"""MM-2：`_load_strategy` 同名包冲突检测（19 号 §135.2 MM-2）。

护栏（§135.2 MM-2 单测三例）：
    ① 先手工 import 一个异源同名假包再装载 ⇒ 必 `ConfigError`（不静默沿用缓存）；
    ② 缓存来源与注入目录同源 ⇒ 不受影响（同源复用放行）；
    ③ 未声明 `strategy_paths` ⇒ 检测不触发（行为与 LL-2 前一致）；
    另：幂等注入行为不变由 `test_strategy_paths.py` 现有 5 例守卫（不回退）。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from btf.config.validation import ConfigError
from btf.runtime import BTFRuntime

DEMO = '''\
from btf.domain.contracts import CONTRACT_VERSION
from btf.strategy.base import StrategyBase


class DemoStrategy(StrategyBase):
    contract_version = CONTRACT_VERSION

    def __init__(self, **_params):
        super().__init__()
'''


def _runtime() -> BTFRuntime:
    rt = BTFRuntime()
    rt.index_universe = None
    return rt


def _make_pkg(root: Path, name: str) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(DEMO, encoding="utf-8")
    return pkg


class TestLoadConflictDetection:
    def test_conflicting_cached_package_rejected(self, tmp_path, monkeypatch):
        """① 异源同名包已缓存 ⇒ ConfigError（文案含两侧路径与唯一包名提示）。"""
        evil_root = tmp_path / "evil_root"
        _make_pkg(evil_root, "mm2_conflict_pkg")
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.syspath_prepend(str(evil_root))
        importlib.import_module("mm2_conflict_pkg")     # 模拟同进程先装载异源同名包
        try:
            rt = _runtime()
            cfg = {"strategy": "mm2_conflict_pkg:DemoStrategy",
                   "strategy_paths": [str(other)]}
            with pytest.raises(ConfigError, match="包名冲突：请使用唯一包名"):
                rt._load_strategy(cfg)
        finally:
            sys.modules.pop("mm2_conflict_pkg", None)

    def test_same_origin_cached_package_allowed(self, tmp_path, monkeypatch):
        """② 缓存来源与注入目录同源 ⇒ 放行（__file__ 父目录在注入目录之下）。"""
        root = tmp_path / "root"
        _make_pkg(root, "mm2_same_pkg")
        monkeypatch.syspath_prepend(str(root))
        importlib.import_module("mm2_same_pkg")          # 先从 root 缓存
        try:
            rt = _runtime()
            cfg = {"strategy": "mm2_same_pkg:DemoStrategy",
                   "strategy_paths": [str(root)]}
            strategy = rt._load_strategy(cfg)
            assert type(strategy).__name__ == "DemoStrategy"
        finally:
            sys.modules.pop("mm2_same_pkg", None)

    def test_no_strategy_paths_skips_detection(self, tmp_path, monkeypatch):
        """③ 未声明 strategy_paths ⇒ 不检测（仓内/环境已可导入模块照常装载）。"""
        ambient_root = tmp_path / "ambient_root"
        _make_pkg(ambient_root, "mm2_ambient_pkg")
        monkeypatch.syspath_prepend(str(ambient_root))
        importlib.import_module("mm2_ambient_pkg")
        try:
            rt = _runtime()
            cfg = {"strategy": "mm2_ambient_pkg:DemoStrategy"}  # 无 strategy_paths
            strategy = rt._load_strategy(cfg)
            assert type(strategy).__name__ == "DemoStrategy"
        finally:
            sys.modules.pop("mm2_ambient_pkg", None)
