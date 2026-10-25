# -*- coding: utf-8 -*-
"""LL-2：`run.strategy_paths` 外部策略装载（19 号 §133.2 LL-2）。

护栏：
    ① 绝对路径注入后 `module:Class` 可加载；
    ② 路径不存在 → `ConfigError`（fail-closed，不静默）；
    ③ 相对路径 → `ConfigError`（防 CWD 漂移破坏跨机可复现）；
    ④ **幂等**：重复装载不重复注入 `sys.path`；
    ⑤ 不声明 `strategy_paths` 时行为不变（仓内夹具仍可加载）。
"""
from __future__ import annotations

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


@pytest.fixture()
def external(tmp_path: Path) -> Path:
    d = tmp_path / "external"
    d.mkdir()
    (d / "ext_demo.py").write_text(DEMO, encoding="utf-8")
    yield d
    # MM-2（19 号 §135.2）：冲突检测上线后，同名模块跨用例缓存自上一用例的
    # tmp 目录会被判「异源同名」而拒载——用例间清理缓存，属测试卫生非语义放宽。
    sys.modules.pop("ext_demo", None)


class TestStrategyPaths:
    def test_absolute_path_loads(self, external: Path, monkeypatch):
        monkeypatch.setattr(sys, "path", list(sys.path))
        rt = _runtime()
        cfg = {"strategy": "ext_demo:DemoStrategy",
               "strategy_paths": [str(external)]}
        strategy = rt._load_strategy(cfg)
        assert type(strategy).__name__ == "DemoStrategy"

    def test_missing_path_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(sys, "path", list(sys.path))
        rt = _runtime()
        cfg = {"strategy": "ext_demo:DemoStrategy",
               "strategy_paths": [str(tmp_path / "missing")]}
        with pytest.raises(ConfigError, match="路径不存在"):
            rt._load_strategy(cfg)

    def test_relative_path_raises(self, monkeypatch):
        monkeypatch.setattr(sys, "path", list(sys.path))
        rt = _runtime()
        cfg = {"strategy": "ext_demo:DemoStrategy",
               "strategy_paths": ["relative/path"]}
        with pytest.raises(ConfigError, match="须为绝对路径"):
            rt._load_strategy(cfg)

    def test_idempotent_injection(self, external: Path, monkeypatch):
        monkeypatch.setattr(sys, "path", list(sys.path))
        rt = _runtime()
        cfg = {"strategy": "ext_demo:DemoStrategy",
               "strategy_paths": [str(external)]}
        rt._load_strategy(cfg)
        resolved = str(external.resolve())
        assert sys.path.count(resolved) == 1
        # 第二次装载不得重复插入
        rt._load_strategy(cfg)
        assert sys.path.count(resolved) == 1

    def test_no_paths_regression(self, monkeypatch):
        """不声明 strategy_paths：仓内测试夹具仍可加载（行为不变）。"""
        monkeypatch.setattr(sys, "path", list(sys.path))
        rt = _runtime()
        cfg = {"strategy": "tests.fixtures.buyhold:ConfigBuyHold"}
        strategy = rt._load_strategy(cfg)
        assert type(strategy).__name__ == "ConfigBuyHold"
