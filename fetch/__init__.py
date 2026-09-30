# -*- coding: utf-8 -*-
"""
fetch/ — 数据采集子包
=====================
公共 API:
  - get_pro: 获取 Tushare Pro 连接
  - get_latest_trade_date: 获取最近交易日（fetch/base.py，日历口径）
  （run_fetch re-export 已随旧采集链归档移除，2026-09-25 F4）
"""

# 延迟导入, 避免循环依赖和启动开销
def __getattr__(name):
    if name == "get_pro":
        from fetch.base import get_pro
        return get_pro
    if name == "get_latest_trade_date":
        from fetch.base import get_latest_trade_date
        return get_latest_trade_date
    raise AttributeError(f"module 'fetch' has no attribute '{name}'")
