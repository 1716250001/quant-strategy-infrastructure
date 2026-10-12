# -*- coding: utf-8 -*-
"""Position / Portfolio / PortfolioSnapshot（04 §8.2.6，名义记账基）。

记账四式（04 §8.2.6 S1 规格，引擎唯一入账路径，黄金集 G1 第一断言；
本模块实现与公式块**逐行对照**——改动任何一行必须先改架构文档，H4 教训）：

    买入：avg_cost' = (avg_cost × qty + fill_price × fill_qty + fee.total)
                      / (qty + fill_qty)
    卖出：realized_pnl += (fill_price − avg_cost) × fill_qty − fee.total
    现金：cash −= (fill_price × fill_qty + fee.total)    [买入]
          cash += (fill_price × fill_qty − fee.total)    [卖出]
    重估：unrealized_pnl = (close_名义 − avg_cost) × qty （每 bar 按名义收盘价）

守恒不变式（与 adj_factor 无关，恒成立；属性测试锚点）：
    每笔 Fill 后：Δcash + ΔMV(名义) + fee.total = 0

NAV 四式（快照字段唯一计算公式，N5-1；参考计算器对账锚点）：
    total_value_t = cash_t + Σ qty_i × close_i
    daily_return_t = total_value_t / total_value_{t−1} − 1（首日 = 0）
    cumulative_return_t = total_value_t / total_value_0 − 1
    drawdown_t = total_value_t / max(total_value_0..t) − 1

收益归集警示（N5-1 加法禁区）：realized_pnl 已通过 avg_cost 摊薄吸收分红；
禁止「已实现收益 = realized_pnl + 分红现金」式相加。总收益一律 total_value 口径。

T+1 冻结：买入不增 available_qty（t_plus=1），advance_day() 于新交易日
开盘前同步 available_qty = qty；t_plus=0（债券/黄金/跨境/货币 ETF）立即可用。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from btf.domain.action import CorporateAction, ratio_or_default
from btf.domain.orders import Fill, OrderSide
from btf.domain.types import TradingDate

logger = logging.getLogger(__name__)

#: 快照权重表中现金项的保留键（04 §8.2.6 "当日权重（含现金）"）
CASH_KEY = "@CASH"


@dataclass
class Position:
    """持仓（引擎内可变状态；对快照只读视图）。记账口径=不复权名义价。"""

    symbol: str
    qty: int = 0                    # 总持仓（股）；除权日经事件调整（PoC-3）
    available_qty: int = 0          # T+N 可卖数量（当日买入冻结，t_plus=1）
    avg_cost: float = 0.0           # 名义价持仓成本（含费用）
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0     # 每 bar 按名义 close 重估

    # ── 记账四式（买入/卖出只动持仓空间；现金在 Portfolio.apply_fill）──
    def buy(self, fill_price: float, fill_qty: int, fee_total: float) -> None:
        """买入：成本加权（公式块第一式）。"""
        new_qty = self.qty + fill_qty
        self.avg_cost = (
            (self.avg_cost * self.qty + fill_price * fill_qty + fee_total) / new_qty
            if new_qty else 0.0
        )
        self.qty = new_qty

    def sell(self, fill_price: float, fill_qty: int, fee_total: float) -> None:
        """卖出：名义价差入已实现（公式块第二式）。"""
        self.realized_pnl += (fill_price - self.avg_cost) * fill_qty - fee_total
        self.qty -= fill_qty
        self.available_qty -= fill_qty
        if self.qty == 0:
            # 空仓归位（04：空仓即移除——由 Portfolio 删除；此处字段复位）
            self.avg_cost = 0.0
            self.available_qty = 0

    def mark(self, close: float) -> None:
        """重估（公式块第四式）。"""
        self.unrealized_pnl = (close - self.avg_cost) * self.qty if self.qty else 0.0


@dataclass(frozen=True)
class PortfolioSnapshot:
    """组合日快照（分析/可视化数据契约的源；公式=N5-1 四式）。"""

    date: TradingDate
    cash: float
    market_value: float             # 持仓市值（不含现金，N5-2 消歧）
    total_value: float              # = cash + market_value（NAV）
    weights: Mapping[str, float]    # 当日权重（含现金项 CASH_KEY）
    positions_qty: Mapping[str, int]
    daily_return: float
    cumulative_return: float
    drawdown: float


@dataclass
class Portfolio:
    """组合：持仓集合 + 现金 + 快照状态机。

    命名纪律（N5-2）：total_value() = NAV；快照 market_value = 持仓市值。
    空仓即移除（快照仍保留历史轨迹）。
    """

    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    # 重估基准价（mark_all 维护；total_value/snapshot 消费）
    _last_close: dict[str, float] = field(default_factory=dict)
    _tv0: float | None = None       # total_value_0（cumulative 基点）
    _tv_prev: float | None = None   # total_value_{t-1}
    _tv_peak: float | None = None   # max(total_value_0..t)
    #: **CL-2 第二段（R5′，19 号 §79.4）**：配股行权审计（逐事件：
    #: 获配股数 / 缴款额 / 现金缺口 / 上市日 / 理论除权价）——引擎转 run 元数据。
    rights_events: list[dict[str, Any]] = field(default_factory=list)
    #: 缩股（R6′ 代理检测）审计（逐事件）。
    consolidations: list[dict[str, Any]] = field(default_factory=list)
    #: **CL-2-H（R32′；ADR-20 第 2 条）**：派息现金到账审计（逐事件：标的 /
    #: 除息日 / 到账日 / `qty_at_ex` / 每份金额 / 到账额）——覆盖段与归因消费。
    dividend_events: list[dict[str, Any]] = field(default_factory=list)

    # ── 入账（唯一路径：撮合成交 → apply_fill）──
    def apply_fill(self, fill: Fill, *, t_plus: int = 1) -> None:
        """成交入账：现金式（第三式）+ 持仓式（第一/二式）+ T+N 冻结。

        t_plus 由 BoardRule 提供（04 §8.2.2 E2：股票/股票ETF=1，
        债券/黄金/跨境/货币 ETF=0）。
        """
        fee = fill.fee.total
        if fill.side is OrderSide.BUY:
            pos = self.positions.get(fill.symbol)
            if pos is None:
                pos = Position(symbol=fill.symbol)
                self.positions[fill.symbol] = pos
            pos.buy(fill.price, fill.qty, fee)
            self.cash -= fill.price * fill.qty + fee
            if t_plus == 0:
                pos.available_qty += fill.qty   # T+0 品种当日可卖
        else:
            pos = self.positions.get(fill.symbol)
            if pos is None or pos.available_qty < fill.qty:
                raise RuntimeError(
                    f"卖出违例（应由 ExecutionHandler 拒单）: {fill.symbol} "
                    f"可卖 {0 if pos is None else pos.available_qty} < {fill.qty}"
                )
            pos.sell(fill.price, fill.qty, fee)
            self.cash += fill.price * fill.qty - fee
            if pos.qty == 0:
                del self.positions[fill.symbol]  # 空仓即移除

    def advance_day(self) -> None:
        """新交易日开盘前：T+1 解冻（available_qty ← qty）。"""
        for pos in self.positions.values():
            pos.available_qty = pos.qty

    # ── 公司行动两时点（PoC-3 任务 3.2；公式唯一出处 04 §8.2.6 S1 规格）──
    def apply_ex_date(self, action: CorporateAction) -> int:
        """ex_date 开盘前调整：派息摊成本 + 送转调 qty/avg_cost/available_qty。

        返回 qty_at_ex（送转调整**前**持仓数——A 股现金红利按登记日
        在册股数发放，除权日新增送转股不参与本次派息）；无持仓 → 0。

        逐行对照 04 §8.2.6（两式分别对应纯送转/纯派息）：
            纯送转：qty ×= (1 + stk_div)；avg_cost /= (1 + stk_div)
                    available_qty ×= (1 + stk_div)          [终审实现注意①]
            纯派息：avg_cost −= cash_div                    [ex 时点只摊成本]
        混合行动（同日送转+派息）按 A 股除权价公式合成：
            avg_cost' = (avg_cost − cash_div) / (1 + stk_div)
        （与除权价 (pre_close − cash_div)/(1+stk_div) 同构——成本与价格
        同空间，总成本账平：qty'·avg_cost' = qty·avg_cost − qty_at_ex·cash_div；
        若先送转后派息会按送转后股数摊薄，多摊 qty·stk_div 份，账不平）。
        qty 取整：round(qty×(1+stk_div))——送转比例千分位（如 1.235）
        产生零头股，四舍五入到股；整比例场景与理论值精确一致。
        """
        pos = self.positions.get(action.symbol)
        if pos is None:
            return 0
        qty_at_ex = pos.qty

        # ── CL-2 第二段（R4′；19 号 §79.2 公式块；Q-4(a) 默认全额行权）──
        # 配股：**原子入账**——份额增加与现金流出**同一时点**（不可拆分：
        # 先扣现金会造成除权日前先跌一次、除权日再跌一次的**双重计数**）。
        #   M = floor(N × ratio)；qty += M；available_qty += M；
        #   cash −= M × 配股价；avg_cost' = (N·avg_cost + M·Pr)/(N+M)
        #   ⚠ **禁止**沿用送转的 `avg_cost /= (1+r)`（那会平白降成本、虚增浮盈）。
        # 除权日以**理论除权价** `(P0 + r·Pr)/(1+r)` 重估该标的：
        #   当日有 bar ⇒ 步骤⑤ mark_all 以真实收盘价覆盖（真实价胜）；
        #   无 bar（缴款期停牌未复牌）⇒ 避免用停牌前价高估 NAV。
        #   等价性自证（§79.2 #3）：(1+r)·Pex = P0 + r·Pr ⇒ 除权日 NAV ≈ 不变。
        # 零回归：`rights_ratio == 0`（旧事件）整块跳过，路径逐位不变。
        ratio = float(getattr(action, "rights_ratio", 0.0) or 0.0)
        if ratio > 0.0 and qty_at_ex > 0:
            price = float(getattr(action, "rights_price", 0.0) or 0.0)
            m = int(qty_at_ex * ratio)          # 可配股数（§79.3 #1 默认 floor）
            if m > 0:
                cost = m * price
                p0 = self._last_close.get(action.symbol)
                old_cost = pos.avg_cost
                #: **B-02（三轮架构审查）**：兜底来源显式登记 —— 当日无 bar 时
                #: `p0` 取 `avg_cost`（**成本冒充市价**），属降级口径，必须
                #: 经 `_corp_action_notes` 进报告「假设与披露」（与 RC-04 同机制），
                #: 不得只在代码注释里自认。
                p0_source = "last_close"
                if p0 is None or p0 <= 0:
                    p0 = old_cost
                    p0_source = "avg_cost_fallback"
                cash_before = self.cash
                pos.qty += m
                pos.available_qty += m
                self.cash -= cost                   # 默认外部增资补足（可透支）
                pos.avg_cost = ((old_cost * qty_at_ex + cost)
                                / (qty_at_ex + m))
                pex = (p0 + ratio * price) / (1.0 + ratio) if p0 > 0 else None
                # **R19′（Q-6 §85.3）**：清单 L2（effective_ratio）下发的 `pex`
                # 优先——**仅无 bar 时生效**（有 bar ⇒ 步骤⑤ mark_all 以真实收盘
                # 覆盖，真实价胜；G7）；None ⇒ 引擎自算（L3 同式，零回归）。
                override = getattr(action, "pex_override", None)
                if override:
                    pex = float(override)
                if pex is not None:
                    self._last_close[action.symbol] = pex
                listing = getattr(action, "rights_listing_date", None)
                self.rights_events.append({
                    "symbol": action.symbol,
                    "ex_date": action.ex_date.to_ymd(),
                    "ratio": ratio, "price": price,
                    "qty_at_ex": qty_at_ex, "rights_qty": m, "cost": cost,
                    "cash_shortfall": max(0.0, cost - cash_before),
                    "p0": p0, "pex": pex,
                    # B-02：`last_close`（正常）／`avg_cost_fallback`（降级，
                    # 当日无 bar ⇒ 成本冒充市价）——报告层据此进披露通道
                    "p0_source": p0_source,
                    "listing_date": (listing.to_ymd() if listing else None),
                })

        # 缩股（**R6′** 代理检测 / **R18′** 股本级确证；默认不触发）：qty 按比例
        # 减少、**无现金流**；成本总额守恒 ⇒ 每股成本按 1/ratio 放大；理论价
        # 同步上移 `P0 / ratio`（有 bar 时被真实收盘覆盖）。`ratio == 1` 跳过。
        # **R21′（19 号 §87.8.1；N22）保底分支**（`int(qty×cons) == 0`）：
        #   ① `avg_cost` **保持不变**（每股成本守恒——放大与理论价上移叠加会
        #      「凭空造股＋浮盈」）；② 事件 `qty_after` 取**保底后实际值**；
        #   ③ 置 `forced_min_qty: true`（WARN 披露，不静默）。
        # **R22′（§87.8.2）**：事件带 `source`（`confirmed`／`proxy`），供逐事件
        #   披露按来源分措辞（确证缩股不得被写成「代理／未确证」）。
        # **R54′（N58/N59 合并，2026-10-08；§102.8）**：消费端经**统一归一化**——
        # `None` ⇒ 1.0（无缩股）；不可转换/非有限 ⇒ 1.0 ＋ WARN（禁崩）；
        # **`≤0` ⇒ 视为无效值 ⇒ 不建模（份额不变）＋ WARN**（老大裁决 (a)：
        # **禁走 R21′ 保底清零**——清零持仓是不可逆的资产级破坏）。
        cons = ratio_or_default(
            getattr(action, "consolidation_ratio", None),
            context=f"portfolio:{action.symbol}:{action.ex_date.to_ymd()}",
            warn=logger.warning)
        if cons != 1.0 and qty_at_ex > 0:
            raw_qty = int(qty_at_ex * cons)
            # **R45′（N48，2026-10-08）**：碎份口径＝**向下取整、丢弃不补偿**
            #（ADR-20 第 ⑪ 条）——丢弃量逐事件披露（`qty_fraction_dropped`），
            # 不得静默消失（510310 实测 777,700 × 0.499844 = 388,728.7 ⇒ 丢 0.7 份）。
            frac_dropped = qty_at_ex * cons - raw_qty
            forced = raw_qty <= 0                      # R21′ 保底判定（保底前）
            new_qty = 1 if forced else raw_qty
            p0c = self._last_close.get(action.symbol)
            if p0c is None or p0c <= 0:
                p0c = pos.avg_cost
            self.consolidations.append({
                "symbol": action.symbol,
                "ex_date": action.ex_date.to_ymd(),
                "ratio": cons, "qty_before": qty_at_ex, "qty_after": new_qty,
                "p0": p0c,
                "p_resume_theory": (p0c / cons if p0c > 0 and cons > 0 else None),
                "source": str(getattr(action, "consolidation_source", "proxy")
                              or "proxy"),
                **({"forced_min_qty": True} if forced else {}),
                # R45′（N48）：碎份丢弃量（向下取整差；保底路径不适用）
                **({"qty_fraction_dropped": round(frac_dropped, 6)}
                   if not forced and frac_dropped > 1e-9 else {}),
            })
            if not forced:
                # 正常路径（**逐位不变**——R21′-C1）：成本总额守恒 ⇒ 每股放大
                pos.avg_cost = pos.avg_cost * qty_at_ex / new_qty
            # 保底路径：`avg_cost` 保持不变（R21′①——每股成本守恒）
            pos.available_qty = int(pos.available_qty * cons)
            pos.qty = new_qty
            pos.available_qty = min(pos.available_qty, pos.qty)
            if p0c > 0 and cons > 0:
                self._last_close[action.symbol] = p0c / cons

        cd = action.cash_div_per_share
        d = action.stk_div_per_share
        if cd:
            pos.avg_cost -= cd
        if d:
            factor = 1.0 + d
            # ── 送转取整口径（**B-01**，2026-10-11 三轮架构审查）──
            # `round()`＝**银行家舍入**（.5 进偶数）——**不是**本注释旧写的
            # 「四舍五入」（原文与实现不符，B-01 已订正）。
            # 与碎份治理框架（ADR-20 第 ⑪ 条）的关系：缩股碎份＝**向下取整 ＋
            # 丢弃量披露**（权益减损方向，保守）；送转零股＝**round**（份额增加
            # 方向，零股属应得补偿，取确定性无偏舍入）。二者**刻意不同口径**，
            # 已在 ADR-20 第 ⑪ 条补注登记（含 .5 边界行为）。
            # ⚠ 改动此口径＝**口径变更**：须走 CHANGELOG 单列 ＋ 变化清单
            #   （照 19 号 §120.2 先例），并同步 `tools/golden_refcalc.py`
            #   与黄金集断言（`tests/regression/test_golden_l3_reconcile.py`）。
            #   黄金用例：`tests/unit/test_portfolio_l1.py` 的 .5 边界组
            #   （qty=101/103 ＋ 10送5 ⇒ 151.5→152、154.5→154）。
            pos.qty = round(pos.qty * factor)
            pos.available_qty = round(pos.available_qty * factor)
            pos.avg_cost /= factor
        return qty_at_ex

    def apply_pay_date(self, symbol: str, qty_at_ex: int, cash_div: float) -> float:
        """pay_date 现金到账：cash += qty_at_ex × cash_div（按登记数量）。

        返回到账金额（0=无登记）。qty_at_ex 跨窗口 pending 由引擎登记
        （终审实现注意②：清仓后仍按登记数量发放）。
        """
        amount = qty_at_ex * cash_div
        self.cash += amount
        return amount

    # ── 估值 ──
    def mark_all(self, closes: Mapping[str, float]) -> None:
        """步骤⑤重估：名义收盘价刷新 unrealized_pnl 与估值基准。"""
        for sym, close in closes.items():
            self._last_close[sym] = close
            pos = self.positions.get(sym)
            if pos is not None:
                pos.mark(close)

    def market_value(self) -> float:
        """持仓市值（不含现金）。"""
        return sum(
            pos.qty * self._last_close.get(sym, pos.avg_cost)
            for sym, pos in self.positions.items()
        )

    def total_value(self) -> float:
        """NAV = cash + Σ qty × close_名义（公式块 N5-1 第一式）。"""
        return self.cash + self.market_value()

    def close_of(self, symbol: str) -> float | None:
        """决策时点盯市价（风控预估用；无价返回 None 由规则降级）。

        持仓标的回退 avg_cost（与 market_value 同口径），未持有且无
        盯市记录 → None。
        """
        if symbol in self._last_close:
            return self._last_close[symbol]
        pos = self.positions.get(symbol)
        return pos.avg_cost if pos is not None else None

    # ── 快照（步骤⑩；N5-1 四式）──
    def snapshot(self, date: TradingDate) -> PortfolioSnapshot:
        # 单遍融合（B3 十年尺度第四刀，CP1 性能专项）：total_value /
        # market_value / weights 原三次遍历持仓（全市场口径 ~3000×3/日，
        # 实测 0.8s/年）合并为一次——数值语义逐位不变（float 求和顺序同序）。
        market = 0.0
        values: dict[str, float] = {}
        qtys: dict[str, int] = {}
        for sym, pos in self.positions.items():
            if not pos.qty:
                continue
            value = pos.qty * self._last_close.get(sym, pos.avg_cost)
            values[sym] = value
            qtys[sym] = pos.qty          # PF-8：持仓数量同遍产出（原二次遍历）
            market += value
        tv = self.cash + market
        if self._tv0 is None:
            self._tv0 = tv
        if self._tv_peak is None or tv > self._tv_peak:
            self._tv_peak = tv
        daily = 0.0 if self._tv_prev is None else tv / self._tv_prev - 1.0
        cum = tv / self._tv0 - 1.0 if self._tv0 else 0.0
        dd = tv / self._tv_peak - 1.0 if self._tv_peak else 0.0
        self._tv_prev = tv
        weights: dict[str, float] = (
            {sym: value / tv for sym, value in values.items()} if tv else {})
        if tv:
            weights[CASH_KEY] = self.cash / tv
        return PortfolioSnapshot(
            date=date, cash=self.cash, market_value=market, total_value=tv,
            weights=weights,
            positions_qty=qtys,          # 单遍产出（19 号审查 PF-8/P3-2 注释一致）
            daily_return=daily, cumulative_return=cum, drawdown=dd,
        )

    # ── 便捷 ──
    def position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def available_qty(self, symbol: str) -> int:
        pos = self.positions.get(symbol)
        return 0 if pos is None else pos.available_qty


__all__ = ["CASH_KEY", "Portfolio", "PortfolioSnapshot", "Position"]
