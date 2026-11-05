# -*- coding: utf-8 -*-
"""cli — 主工程命令层（W7b 起自 main.py 拆出）。"""
from cli.app import main
from cli.parser import build_parser

__all__ = ["build_parser", "main"]
