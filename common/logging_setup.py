# -*- coding: utf-8 -*-
"""
common/logging_setup.py — 统一日志初始化
=========================================
此前 push/intraday_monitor.py 在 import 时就 logging.basicConfig() 并创建
文件 handler，导致"只要 import 就产生日志文件"的副作用。

改为：由各入口在 main() 中显式调用 setup_logging()，且保证幂等
（重复调用不会叠加 handler）。
"""
import logging
import os
import sys

from common.paths import LOG_DIR

_configured = False


def setup_logging(name="teleagent", level=logging.INFO, log_file=None, console=True):
    """配置根 logger 并返回命名 logger。

    参数:
        name: 返回的 logger 名
        level: 日志级别
        log_file: 文件名（相对 LOG_DIR）或绝对路径；None = 仅控制台
        console: 是否输出到 stdout
    返回:
        logging.Logger
    幂等：同一进程内重复调用不会重复添加 handler。
    """
    global _configured
    logger = logging.getLogger(name)
    if _configured:
        return logger

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    if console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    if log_file:
        if not os.path.isabs(log_file):
            os.makedirs(LOG_DIR, exist_ok=True)
            log_file = os.path.join(LOG_DIR, log_file)
        else:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    _configured = True
    return logger
