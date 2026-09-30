# -*- coding: utf-8 -*-
"""L3 分析层：Analyzer 绩效指标（纯函数）。

契约（04 §8.3.6 + 04 §8.2.6 N5-1）：
    指标计算唯一入口为 NAV 四式（total_value/daily_return/cumulative_return/drawdown）
    收益归集加法禁区（N5）：realized_pnl 已摊薄吸收分红，禁止与分红现金相加
    15 项核心指标：年化/波动/Sharpe/最大回撤/Calmar/胜率/盈亏比/换手/费用占比等

纪律：无副作用；指标异常→NaN 不中断（降级策略）。
研究层（向量化）v0.5 交付（ADR-10），首用例=基本面因子研究。
"""
