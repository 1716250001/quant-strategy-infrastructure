# -*- coding: utf-8 -*-
r"""策略契约常量夹具（MM-3，19 号 §135.2）——btf 侧文档守卫的唯一取值来源。

为什么存在：`QidianStrategy` 实现在仓外（`赤潮/研究/cc_strategies/`，读法 B），
btf 文档守卫**不得 import 仓外包**（MM-3 前经仓外 import 取值，包名撞车后
沦为全量唯一 skip——守卫空转）。故把「文档声明值 ↔ 策略实现」的互校拆成双侧：

    · btf 侧  `tests/integration/test_doc_claim_guards.py::test_t_b_warmup_value_guard`
      只读本夹具，断言文档数字 == `QIDIAN_LOOKBACK_BARS`；
    · 仓外侧 `cc_strategies/tests/test_contract_sync.py`
      实跑 `QidianStrategy.lookback_bars()`，断言 == 本夹具值（经绝对路径读）。

任一侧漂移必有一侧红（双向可证伪）。

取值依据：`QIDIAN_LOOKBACK_BARS = QidianStrategy.lookback_bars()` 实跑实测
（2026-10-24，btf v1.3.4 工作区）＝ **1020**。
再生成方法：`sys.path.insert(0, <STRATEGY_ROOT>)` 后
`from cc_strategies.qidian_replica import QidianStrategy;
QidianStrategy.lookback_bars()`。
"""
QIDIAN_LOOKBACK_BARS: int = 1020
