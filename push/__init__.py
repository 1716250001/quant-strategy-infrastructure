# -*- coding: utf-8 -*-
"""
push/ — 推送与监控子包
=====================
公共 API:
  - run_scan: 盘中MACD背离扫描入口
  - run_report: 日报生成入口
  - PushPlus: 微信推送类
"""

# 延迟导入, 避免循环依赖和启动开销
def __getattr__(name):
    if name == "run_scan":
        # [ARCHIVED 2026-09-25 G6] intraday_monitor 已随背离链路归档（_archive/背离扫描器-20260924/）
        raise AttributeError("run_scan 已随盘中背离监控归档（2026-09-25），见 _archive/背离扫描器-20260924/")
    if name == "run_report":
        return run_report
    if name == "PushPlus":
        from push.pushplus import PushPlus
        return PushPlus
    raise AttributeError(f"module 'push' has no attribute '{name}'")
