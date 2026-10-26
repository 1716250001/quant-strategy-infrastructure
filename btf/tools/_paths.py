# -*- coding: utf-8 -*-
"""tools/ 私有路径单点派生（ADR-4 在工具层的落地）。

btf 包**不认识「赤潮」这个名字**（零私有语义，D-2 后达成）；
本仓自用工具若要访问私有仓，一律经本模块，禁止各脚本各写一份。

**自包含**：不 import btf——`tools/` 不是包（无 __init__.py），且部分工具
（check_rules_four_way）现状不依赖 btf；本模块必须能被任意 tools 脚本直接导入。
"""
from __future__ import annotations

import os
from pathlib import Path

#: 工作区根：tools → 仓根(btf) → 代码 → 量化策略（四级上溯，实测
#: D:\量化策略\代码\btf\tools\_paths.py ⇒ D:\量化策略）
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

#: 私有仓根（默认由工作区锚点派生；BTF_CHICHAO_ROOT 可覆盖）
CHICHAO_ROOT = Path(os.environ.get("BTF_CHICHAO_ROOT")
                    or _WORKSPACE_ROOT / "赤潮").resolve()
#: 策略实现父目录（run.strategy_paths 用）
STRATEGY_ROOT = Path(os.environ.get("BTF_STRATEGY_ROOT")
                     or CHICHAO_ROOT / "研究").resolve()
#: 规则镜像（rules.path 用）
RULES_MIRROR = Path(os.environ.get("BTF_RULES_MIRROR")
                    or CHICHAO_ROOT / "rules_mirror_v77.json").resolve()
#: 备份根（冷备产物默认落点；BTF_BACKUP_ROOT 可覆盖）
BACKUP_ROOT = Path(os.environ.get("BTF_BACKUP_ROOT")
                   or _WORKSPACE_ROOT / "备份").resolve()
#: 临时产物的**唯一默认落点**（审计/检查工具的 CSV、缓存等；BTF_SCRATCH_ROOT 可覆盖）。
#: **不得**再让各工具写进仓内（§176：`stkdiv_audit` 三次污染仓内的根因＝缺本锚点）。
#: 既有惯例：`check.py` 日志、pytest `--basetemp` 均落 `代码/_scratch/`。
SCRATCH_ROOT = Path(os.environ.get("BTF_SCRATCH_ROOT")
                    or _WORKSPACE_ROOT / "代码" / "_scratch").resolve()
