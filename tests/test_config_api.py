# -*- coding: utf-8 -*-
"""tests/test_config_api.py — 配置公开面 smoke（W6-DEP-02）。"""
import config

CRITICAL_NAMES = [
    "PROJECT_DIR", "CODE_DIR", "DATA_ROOT",
    "MARKET_DATA_DIR", "ALT_DATA_DIR", "WIND_DATA_DIR",
    "DOC_DIR", "DATA_DIR", "TEMP_DIR",
    "TABLE_DATE_COL", "__version__",
]


def test_critical_constants_exist():
    missing = [n for n in CRITICAL_NAMES if not hasattr(config, n)]
    assert not missing, f"config 缺少关键常量: {missing}"


def test_version_is_string():
    assert isinstance(config.__version__, str) and config.__version__.count(".") >= 2


def test_constants_are_str_not_path():
    """下游 64 处 from config import 期待 str，不是 Path。"""
    for n in ("MARKET_DATA_DIR", "ALT_DATA_DIR", "WIND_DATA_DIR"):
        assert isinstance(getattr(config, n), str), f"{n} 必须是 str"
