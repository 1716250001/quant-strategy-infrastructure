# -*- coding: utf-8 -*-
"""公司行动值对象：CorporateAction（04 §8.2.4 签名照抄）。

PoC-2 仅冻结值对象（事件类型引用它）；引擎接入（ex_date/pay_date 两时点
分发、qty_at_ex 登记）属 PoC-3 任务 3.2。显式调整公式见 04 §8.2.6 S1 规格
（本模块 docstring 只声明语义锚点，公式实现在 portfolio 层，防口径双写）。

零第三方依赖（03 §7.3 铁律 3）。
"""
from __future__ import annotations

from dataclasses import dataclass

from btf.domain.types import TradingDate


@dataclass(frozen=True)
class CorporateAction:
    """公司行动（分红送转派息）。源自 dividend 表，字段含义见 05 数据字典。

    两时点语义（v0.3 N7-A）：ex_date 调份额/成本（开盘前）；
    pay_date 现金到账（仅派息）。2020+ 库内两日常重合，引擎仍按两时点
    独立分发（重合日等效单日），事件日志中各留一条。

    **CL-2 第二段（2026-10-07；19 号 §79.4 R3′）——有偿份额变动扩维**：
    新增 `rights_*`（配股；Q-4(a) 默认全额行权，§79.2 公式块）与
    `consolidation_ratio`（缩股代理，§78.5）——**旧五字段语义与默认值不变**
    （向后兼容：既有构造点/黄金集行为逐位一致）。配股事件的 `ex_date`
    ＝ **本地 `adj_factor` 跳变日**（除权日真值，§77-bis.2 16/16）；
    `rights_ratio` 的基准股数 = **登记日收盘持仓**（引擎 `qty_at_ex`）。
    """

    symbol: str
    ex_date: TradingDate          # 除权除息日（调价格/份额/成本）
    pay_date: TradingDate | None  # 派息日（现金到账；缺失~0.1%→回退 ex_date，E5）
    record_date: TradingDate | None
    cash_div_per_share: float     # 每股派息（税前，元）
    stk_div_per_share: float      # 每股送转（送股+转增合计，股/股）
    ann_date: TradingDate | None  # 公告日（PIT 对齐：策略只能用 ann_date<=当前日的行动）
    # ── CL-2 第二段（默认值 = 无配股/无缩股 ⇒ 旧路径逐位不变）──
    rights_ratio: float = 0.0     # 配股比例（股/股；「10配X」⇒ X/10）
    rights_price: float = 0.0     # 配股价（元/股）
    rights_ex_date: TradingDate | None = None      # 配股除权日（= 跳变真值）
    rights_listing_date: TradingDate | None = None  # 获配股份上市日（缺 ⇒ None）
    consolidation_ratio: float = 1.0  # 缩股比例（qty 乘数；1.0 = 无）


__all__ = ["CorporateAction"]
