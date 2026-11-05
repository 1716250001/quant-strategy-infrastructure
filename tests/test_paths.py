# -*- coding: utf-8 -*-
"""tests/test_paths.py — 三库路径 smoke（W6-DEP-02）。

⚠ test_env_override_works 会 importlib.reload(config)，必须放在文件最后。
"""
import os

import config


def test_three_roots_distinct():
    roots = {config.MARKET_DATA_DIR, config.ALT_DATA_DIR, config.WIND_DATA_DIR}
    assert len(roots) == 3, f"三库根有重复: {roots}"


def test_roots_derived_from_data_root():
    for n in ("MARKET_DATA_DIR", "ALT_DATA_DIR", "WIND_DATA_DIR"):
        assert getattr(config, n).startswith(config.DATA_ROOT), f"{n} 未由 DATA_ROOT 派生"


def test_main_library_exists():
    assert os.path.isdir(config.MARKET_DATA_DIR), "主库目录不存在"


def test_env_override_works(monkeypatch):
    """环境变量可覆盖（W2 的核心能力，防回归）。

    ⚠ W3 后 config 是包，常量真源在 config/paths.py：
      仅 reload(config) 不会重新执行 paths.py，故必须两个都 reload。
    """
    monkeypatch.setenv("CHICHAO_WIND_DATA_DIR", "/tmp/probe_smoke")
    import importlib

    import config as c
    import config.paths as cp
    importlib.reload(cp)
    importlib.reload(c)
    assert c.WIND_DATA_DIR != r"D:\全量数据\wind_data"
