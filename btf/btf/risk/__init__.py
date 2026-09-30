# -*- coding: utf-8 -*-
"""L2 风控层：预交易风控规则链（NautilusTrader RiskEngine 思想）。

契约（04 §8.3.4）：
    rules     RiskRule 插件（ALLOW / REDUCE_TO(qty) / REJECT(code)）
    manager   有序规则链编排，首个 REDUCE/REJECT 生效

MVP 内置三规则：max_weight / cash_check / tradability。
风控在订单进入撮合前；撮合时点拒单在 ExecutionHandler（互补）。
"""
