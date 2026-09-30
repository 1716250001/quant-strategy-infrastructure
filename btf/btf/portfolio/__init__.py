# -*- coding: utf-8 -*-
"""L2 组合层：Portfolio 记账 + Rebalancer 再平衡。

记账口径（04 §8.2.6，名义记账基，全文唯一公式出处）：
    买入：avg_cost' = (avg_cost×qty + fill_price×fill_qty + fee) / (qty+fill_qty)
    卖出：realized_pnl += (fill_price − avg_cost)×fill_qty − fee
    现金/重估/除权事件两时点/NAV 四式/收益归集加法禁区——见架构文档公式块。

命名纪律（N5-2）：Portfolio.total_value()（= NAV）；快照 market_value=持仓市值（不含现金）。
Rebalancer：FullRebalancer 差额整手化 + 卖先买后排序（S2，MVP 级正确性）。
"""
