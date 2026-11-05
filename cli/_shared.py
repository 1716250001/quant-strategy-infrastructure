# -*- coding: utf-8 -*-
"""cli._shared — 命令行公共小工具（W7b 自 main.py 原样搬出）。"""
import datetime


def _today():
    return datetime.datetime.now().strftime("%Y-%m-%d")

def _banner(text):
    """打印与原有风格一致的命令行横幅：[tag] title"""
    tag, _, title = text.partition(" ")
    print("=" * 60)
    print(f"  [{tag}] {title}" if title else f"  [{tag}]")
    print("=" * 60)

def _add_date(p, help_text="目标日期 YYYY-MM-DD (默认: 今天)"):
    p.add_argument("--date", default=None, help=help_text)

def _add_dry_run(p, help_text="只统计不写入"):
    p.add_argument("--dry-run", action="store_true", help=help_text)

def _add_json(p, help_text="输出 JSON（机器可读；不打印横幅/明细）"):
    p.add_argument("--json", action="store_true", dest="json", help=help_text)
