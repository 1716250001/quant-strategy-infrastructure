# -*- coding: utf-8 -*-
"""
common/ — 跨模块公共工具层
==========================
所有重复的底层能力集中于此，供 fetch / push / tools 复用：

  paths           路径解析（唯一真源，禁止再用 __file__ 相对推算）
  jsonio          JSON 读写统一封装（统一编码与异常处理）
  parquet_store   按 ts_code 分文件的 Parquet 增量存储（合并去重）
  calendar        交易日历读取（trade_cal.parquet 唯一入口）
  codes           证券代码格式转换（ts_code / secid / 新浪 symbol）
  logging_setup   日志初始化（由入口显式调用，消除 import 副作用）

导入约定：使用显式子模块导入，如
    from common.jsonio import load_json, save_json
    from common.paths import DATA_DIR, REPORT_DIR, ensure_dir
"""
