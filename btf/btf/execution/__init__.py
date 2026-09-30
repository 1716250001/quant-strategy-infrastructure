# -*- coding: utf-8 -*-
"""L2 执行层：ExecutionHandler + CostModel + SlippageModel。

契约（04 §8.3.3）：
    handler    NextOpen / Close 两种撮合基准（MVP）
    cost       FeeSchedule 日期区间分段（印花税 2023-08-28 两段；过户费三段，
               2015-08 前"仅沪市"经 MarketRule 时间分段表达——终审 E2/N2）
    slippage   FixedSlippage / PctSlippage（插件位）

拒单规则（纯函数）：SUSPENDED / LIMIT_UP / LIMIT_DOWN / T_PLUS_1 /
LOT_SIZE / INSUFFICIENT_CASH（涨跌停浮点容差 1e-4）。
"""
