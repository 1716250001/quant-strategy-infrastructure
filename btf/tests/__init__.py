# -*- coding: utf-8 -*-
"""btf 测试体系（09 号文档五层金字塔）。

    unit/          L1 单元（秒级，提交门）
    integration/   L2 集成（引擎×Feed×撮合组合行为，MemoryFeed）
    system/        L3 系统端到端（参考计算器对账，容差 1e-10）
    regression/    L4 回归（黄金集 G1-G10 + 双跑事件流 diff）
    benchmark/     L5 性能基准（B1-B5，pytest-benchmark）

纪律：黄金集断言只增不改；domain/execution/portfolio/engine 提交必跑 L4。
"""
