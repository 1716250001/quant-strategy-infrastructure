# -*- coding: utf-8 -*-
"""测试侧私有仓路径单点（§161 L-2）。

`tests/` 与 `tools/` 同理：私有语义只在不进发布包的一侧存在。
**不 import `btf`**——避免测试夹具反向依赖被测包；环境变量与
`tools/_paths.py` 同名，便于两侧同源覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

#: tests/fixtures/private_paths.py → fixtures → tests → 仓根 → 代码 → 量化策略
_WORKSPACE_ROOT = Path(__file__).resolve().parents[4]

CHICHAO_ROOT = Path(os.environ.get("BTF_CHICHAO_ROOT")
                    or _WORKSPACE_ROOT / "赤潮").resolve()
STRATEGY_ROOT = Path(os.environ.get("BTF_STRATEGY_ROOT")
                     or CHICHAO_ROOT / "研究").resolve()
RULES_MIRROR = Path(os.environ.get("BTF_RULES_MIRROR")
                    or CHICHAO_ROOT / "rules_mirror_v77.json").resolve()
