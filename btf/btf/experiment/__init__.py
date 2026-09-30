# -*- coding: utf-8 -*-
"""L3 实验层：RunStore 产物管理 + RunManifest 可复现性契约。

契约（08 号文档）：
    store      run_id/ 目录族（snapshots/trades/fills/rejections/events/metrics/manifest）
    manifest   四版本快照（data/code/config/env 运行时实采 E2）+ rules_version（H2）
               启动先写骨架（RUNNING），结束补全——崩溃 run 可审计
    replay     事件回放（JSONL 逐行；采样日志提示降级状态）

可复现性：R1 级=同 manifest 重跑 metrics_digest 一致；锚点截断回放 + 指纹检测 data_revised。
"""
