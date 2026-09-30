# -*- coding: utf-8 -*-
"""BT-Foundation（btf）——A 股量化回测工具。

架构基线：文档/回测工具架构/ v0.3（冻结，五轮评审闭环）。
第一验收场景：v7.7 规则引擎回归 + 奇点战法复跑（见 18 号分步编码计划 M3）。

分层（03 §7.2，依赖箭头只指向更稳定方向：外围→编排→引擎/分析→领域）：
    domain      D 领域层（最稳定，零第三方依赖）
    data        L0 数据层（协议稳定/适配器易变）
    strategy    L1 策略层（基类稳定/用户代码易变）
    engine      L2 事件循环（核心稳定）
    execution   L2 撮合/执行（cost/slippage 插件位）
    risk        L2 预交易风控规则链
    portfolio   L2 组合/再平衡
    analytics   L3 绩效指标（纯函数）
    experiment  L3 RunStore/manifest（可复现性）
    config      C 分层配置 + Schema 校验
    registry    R 配置声明制插件发现
    viz         L5 图表/报告（只消费 RunResult 数据契约）
    cli         L5 命令行入口（只编排不承载业务）

入口：CLI 十一命令（`btf.cli.main`：config-check/run/report/verify/test/dataset/
optimize + **check/cold-backup/runs/show**——后四条见 CLI 审查报告-20260930）
+ `btf.runtime.BTFRuntime` 门面（装配/落盘/复核）
+ **api Facade**（v0.5 V5-7）：`btf.run / btf.report / btf.dataset` 顶层函数
（`btf/api.py` 惰性暴露——与 CLI 同链路等价，见模块 docstring 的等价性口径）。
"""
from btf._version import __version__  # noqa: F401 —— 包根 re-export（惯例接口）


def __getattr__(name: str):
    """api Facade 惰性暴露（import btf 零成本——不拉起数据层）。

    经 importlib 动态加载：`from btf import api` 的静态 import 语句会被
    import-linter 记为包根→子模块边，与子模块取版本的 `from btf import
    __version__`（现已改道 `btf._version`，见该模块 docstring）共同构成
    跨层假链；动态加载不改变运行时语义（btf.api 已列入 layers 契约）。
    """
    if name in ("run", "report", "dataset"):
        from importlib import import_module

        return getattr(import_module("btf.api"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
