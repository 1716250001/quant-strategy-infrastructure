# -*- coding: utf-8 -*-
"""L1 策略层：StrategyBase 基类 + StrategyContext 上下文 + rules_loader。

契约（04 §8.3.2）：
    base       init / on_open / on_close 生命周期钩子
    context    只读数据 + 下单 API（禁止文件/网络 I/O；未来日期访问断言）
    rules      RulesProvider / rules_loader（H2：规则参数单一真源，
               从 md_core 镜像 JSON 加载；YAML 仅 override:true 可覆盖；
               check_rule_mirror.py 四方机检纳管）
    rebalance  TargetPortfolio 声明（信号-执行分离，WonderTrader 思想）
"""
