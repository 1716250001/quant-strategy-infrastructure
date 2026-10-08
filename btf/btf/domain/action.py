# -*- coding: utf-8 -*-
"""公司行动值对象：CorporateAction（04 §8.2.4 签名照抄）。

PoC-2 仅冻结值对象（事件类型引用它）；引擎接入（ex_date/pay_date 两时点
分发、qty_at_ex 登记）属 PoC-3 任务 3.2。显式调整公式见 04 §8.2.6 S1 规格
（本模块 docstring 只声明语义锚点，公式实现在 portfolio 层，防口径双写）。

零第三方依赖（03 §7.3 铁律 3）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from btf.domain.types import TradingDate


def ratio_or_default(
    value: Any,
    *,
    default: float = 1.0,
    field: str = "consolidation_ratio",
    context: str = "",
    warn: Callable[[str], None] | None = None,
) -> float:
    """**R54′（N58＋N59 合并，2026-10-08；19 号 §102.8）**：比率字段**统一归一化**。

    三态语义（**禁崩、禁静默、禁清零**）：

    1. `None` ⇒ `default`（无值＝无该行动，静默——旧行为不变）；
    2. **不可转换为 float**（`""`／`"abc"`／NaN／∞ 等）⇒ `default` ＋ **WARN**
       （N58：`is not None` 只兜 `None`，`float("")` 直接 `ValueError` 崩溃）；
    3. **转换后 ≤ 0** ⇒ 视为**无效值 ⇒ 不建模（份额不变）**⇒ 取 `default`
       ＋ **WARN**——**禁止**流入缩股分支的 R21′ 保底（`int(qty×0)=0` ⇒ 持仓被
       清零至 1 股）。**老大 2026-10-08 裁决 (a)**：宁可漏记一次缩股，
       不可摧毁持仓（§102.1）。

    背景（§102.6 自我勘误 #17）：此处既不该用 `float(x or 1.0)`（吞 `0.0`），
    也不该用 `is not None`（不兜 falsy 脏值）——**读写同一入口**才是正确形态。

    **告警注入 `warn`**（`Callable[[str], None] | None`）：决定「何时告警」的策略
    在本函数**单点**；**发射**由调用方注入（通常 `logger.warning`）——`btf/domain`
    受 03 §7.3 铁律 3 白名单约束（不得 import `logging`），且域层不持全局副作用。
    `warn=None` ⇒ 静默（仅取值）。

    注意：本函数假设 `default` 本身合法且 `> 0`（当前唯一用途＝缩股比，
    `default` 恒为 1.0＝「无该行动」）。
    """
    tag = field + (f"@{context}" if context else "")

    def _emit(msg: str) -> None:
        if warn is not None:
            warn(msg)

    if value is None:
        return float(default)
    try:
        num = float(value)
    except (TypeError, ValueError):
        _emit(f"公司行动 {tag} 不可转换为数值（{value!r}）⇒ 取默认 {default}"
              f"（R54′/N58：禁崩、禁静默）")
        return float(default)
    # NaN／±∞（不依赖 math：NaN 自比不相等；±inf 显式比较）
    if num != num or num in (float("inf"), float("-inf")):
        _emit(f"公司行动 {tag} 非有限值（{value!r}）⇒ 取默认 {default}（R54′）")
        return float(default)
    if num <= 0:
        _emit(f"公司行动 {tag} ≤0（{value!r}）⇒ 视为**无效值、不建模**"
              f"（份额不变）＋WARN（R54′/N59 裁决 (a)：禁走保底清零）")
        return float(default)
    return num


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
    #: **R22′（§87.8.2）**：缩股来源——`confirmed`（股本级三重证据，R18′，
    #: 来自 `consolidations_confirmed`）／`proxy`（价格代理检测，R6′，仅披露）。
    consolidation_source: str = "proxy"
    # ── R19′（Q-6，1.2.0 修订）──清单 L2（effective_ratio）下发的 `pex`；
    # 引擎**仅在除权日无 bar** 时采用（有 bar 真实收盘胜，G7）；None = 引擎自算。
    pex_override: float | None = None


__all__ = ["CorporateAction", "ratio_or_default"]
