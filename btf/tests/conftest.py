# -*- coding: utf-8 -*-
"""pytest 全局夹具：tools/ 脚本目录入 sys.path（构建器/参考计算器单测用）。"""
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
