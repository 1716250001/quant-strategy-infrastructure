# -*- coding: utf-8 -*-
"""cli.app — 入口 main()（W7b 自 main.py 原样搬出，含 setup_logging）。"""
import sys

from cli.parser import build_parser
from common.logging_setup import setup_logging


def main(argv=None):
    setup_logging("chichao", log_file="chichao.log")   # 2026-11-05 W6：日志基础设施接通
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return

    rc = args.func(args)
    rc = rc if isinstance(rc, int) else 0
    # --json 模式保持 stdout 为**纯 JSON**（机器可读），不追加完成语
    if not getattr(args, "json", False):
        print("\n完成。")
    sys.exit(rc)
