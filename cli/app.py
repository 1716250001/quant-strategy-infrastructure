# -*- coding: utf-8 -*-
"""cli.app — 入口 main()（W7b 自 main.py 原样搬出，含 setup_logging）。"""

import sys

from cli.parser import build_parser
from common.logging_setup import setup_logging


def main(argv=None):
    setup_logging("chichao", log_file="chichao.log")  # 2026-11-05 W6：日志基础设施接通
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return

    rc = args.func(args)
    if rc is None:  # 只读命令兼容：未显式返回视为成功
        rc = 0
    elif not isinstance(rc, int):
        print(f"[WARN] 命令返回非整数退出码 {rc!r}，按 0 处理", file=sys.stderr)
        rc = 0
    # --json 模式保持 stdout 为**纯 JSON**（机器可读），不追加完成语
    if not getattr(args, "json", False):
        if rc == 0:
            print("\n完成。")
        else:
            print(f"\n[失败] 退出码 {rc}", file=sys.stderr)
    sys.exit(rc)
