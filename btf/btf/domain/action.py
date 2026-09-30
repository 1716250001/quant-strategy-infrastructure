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
    """

    symbol: str
    ex_date: TradingDate          # 除权除息日（调价格/份额/成本）
    pay_date: TradingDate | None  # 派息日（现金到账；缺失~0.1%→回退 ex_date，E5）
    record_date: TradingDate | None
    cash_div_per_share: float     # 每股派息（税前，元）
    stk_div_per_share: float      # 每股送转（送股+转增合计，股/股）
    ann_date: TradingDate | None  # 公告日（PIT 对齐：策略只能用 ann_date<=当前日的行动）


__all__ = ["CorporateAction"]
