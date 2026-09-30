# -*- coding: utf-8 -*-
"""黄金集 → 引擎回测夹具（M2 任务 5.7 L3 对账；09 §14.3）。

职责：把 `build_golden.py` 产出的 case JSON 还原为**引擎可执行**的输入
（MemoryFeed + instruments + 脚本化目标策略 + 分段费用/撮合组件），使
「独立参考计算器 vs 引擎」端到端对账成为可能。

对账口径（两侧必须用同一套外部条件，差异才归因于实现）：
    - 费用：tiered_v1（分段，与参考计算器倍率/最低佣金/四舍五入同规则）
    - 滑点：none（回测基线）
    - 撮合：next_open（T+1 开盘价；与参考计算器成交口径一致）
    - 再平衡：full（差额法 + 整手 + 卖先买后）
    - 风控：空链（放行 + 告警——避免风控差异混入记账对账）
"""
from __future__ import annotations

import json
from pathlib import Path

from btf.config.paths import DATASETS_DIR
from btf.domain.action import CorporateAction
from btf.domain.market import Bar, TradingState
from btf.domain.orders import Order, OrderSide, OrderType
from btf.domain.types import (
    AssetClass,
    EtfSubclass,
    Instrument,
    TradingDate,
)
from btf.engine.loop import Engine
from btf.execution.cost import AShareTieredFeeModel
from btf.execution.handler import NextOpenHandler
from btf.portfolio.rebalancer import FullRebalancer
from btf.risk.manager import RuleChainManager
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

#: 黄金集 case 目录（05 §20.1 golden_v{N}）
CASES_DIR = DATASETS_DIR / "golden_v1" / "cases"

D = TradingDate.from_ymd


class ScriptedTargets(StrategyBase):
    """按 plan.targets / plan.orders 脚本提交（日期 → 目标 / 显式订单）。

    提交顺序**镜像引擎⑨**：先显式订单后目标（drafts = explicit + 差额法），
    与 `golden_refcalc` 的 pending 顺序逐条对应——顺序差异会改变现金链，
    故此处不可调换（G7 卖先买后资金链断言的成立前提）。
    """

    def __init__(self, targets: dict[str, dict[str, float]],
                 orders: list[dict] | None = None):
        self.targets = targets
        self.orders_by_date: dict[str, list[dict]] = {}
        for order in orders or []:
            self.orders_by_date.setdefault(order["date"], []).append(order)

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        ymd = date.to_ymd()
        for draft in self.orders_by_date.get(ymd, []):
            ctx.submit_order(Order(
                order_id="", symbol=draft["symbol"],
                side=OrderSide(draft["side"]), order_type=OrderType.MARKET,
                qty=int(draft["qty"]), limit_price=None, created_at=date,
                tag="golden_explicit"))
        target = self.targets.get(ymd)
        if target is not None:
            ctx.submit_target(TargetPortfolio(date, target))


def load_case(case_id: str, root: Path | None = None) -> dict:
    """读取 case JSON（含 data/plan/assertions）。"""
    path = (root or CASES_DIR) / f"{case_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"黄金案例缺失（先跑 build_golden）: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _opt_float(value: object) -> float | None:
    return None if value is None else float(value)


def case_to_feed(case: dict) -> tuple[object, dict[str, Instrument]]:
    """case.data → (MemoryFeed, instruments)。

    状态面板：case 显式 `data.states` 优先（G2–G7：真实 stk_limit/停牌/ST/退市）；
    缺省按 `pre_close × (1 ± 10%)` **合成**（G1 口径，涨跌停从未触及）。
    ETF 子类经 `etf_subclass` 注入 → `rule_for` 决定 T+N（E2 双维规则）。
    """
    from btf.data.memory import MemoryFeed

    data = case["data"]
    instruments = {}
    for i in data.get("instruments", []):
        subclass = i.get("etf_subclass")
        instruments[i["symbol"]] = Instrument(
            symbol=i["symbol"],
            asset_class=AssetClass(i.get("asset_class", "stock")),
            name=i.get("name", ""),
            board=i.get("board", "main"),
            lot_size=int(i.get("lot_size", 100)),
            etf_subclass=EtfSubclass(subclass) if subclass else None,
        )
    bars = []
    for b in data["bars"]:
        bars.append(Bar(symbol=b["symbol"], date=D(b["date"]),
                        open=float(b["open"]), high=float(b["high"]),
                        low=float(b["low"]), close=float(b["close"]),
                        volume=float(b.get("vol", 0.0)),
                        amount=float(b.get("amount", 0.0)),
                        pre_close=float(b.get("pre_close", 0.0))))
    states: dict[tuple[str, str], TradingState] = {}
    rows = data.get("states")
    if rows:
        for s in rows:
            states[(s["date"], s["symbol"])] = TradingState(
                symbol=s["symbol"], date=D(s["date"]),
                limit_up_price=_opt_float(s.get("limit_up")),
                limit_down_price=_opt_float(s.get("limit_down")),
                is_suspended=bool(s.get("suspended", False)),
                is_st=bool(s.get("is_st", False)),
                is_delisted=bool(s.get("delisted", False)),
                is_limit_up=False, is_limit_down=False,
            )
    else:
        for b in data["bars"]:
            pre = b.get("pre_close") or b["close"]
            states[(b["date"], b["symbol"])] = TradingState(
                symbol=b["symbol"], date=D(b["date"]),
                limit_up_price=round(float(pre) * 1.1, 2),
                limit_down_price=round(float(pre) * 0.9, 2),
                is_suspended=False, is_st=False, is_delisted=False,
                is_limit_up=False, is_limit_down=False,
            )
    actions = [
        CorporateAction(
            symbol=a["symbol"], ex_date=D(a["ex_date"]),
            pay_date=D(a["pay_date"]) if a.get("pay_date") else None,
            record_date=D(a["record_date"]) if a.get("record_date") else None,
            cash_div_per_share=float(a.get("cash_div_per_share", 0.0)),
            stk_div_per_share=float(a.get("stk_div_per_share", 0.0)),
            ann_date=D(a["ann_date"]) if a.get("ann_date") else None,
        )
        for a in data.get("actions", [])
    ]
    feed = MemoryFeed(bars=bars, states=states, instruments=instruments,
                      dates=[D(d) for d in data["dates"]], actions=actions)
    return feed, instruments


def run_case(case: dict) -> object:
    """执行引擎回测（返回 engine.RunResult）。

    VPP（G10，v0.5 V5-6）：`plan.execution.volume_participation` 存在时
    注入撮合量上限——与参考计算器同一外部条件（对账口径，09 §14.3）。
    """
    feed, instruments = case_to_feed(case)
    dates = [D(d) for d in case["data"]["dates"]]
    strategy = ScriptedTargets(case["plan"]["targets"],
                               case["plan"].get("orders"))
    vpp = (case["plan"].get("execution") or {}).get("volume_participation")
    handler = NextOpenHandler(cost_model=AShareTieredFeeModel(),
                              slippage_model=None, instruments=instruments,
                              volume_participation=vpp)
    engine = Engine(
        feed, strategy, start=dates[0], end=dates[-1],
        initial_cash=float(case["plan"]["initial_cash"]),
        handler=handler, rebalancer=FullRebalancer(instruments=instruments),
        risk_manager=RuleChainManager([]), instruments=instruments,
    )
    return engine.run()


__all__ = ["CASES_DIR", "ScriptedTargets", "case_to_feed", "load_case",
           "run_case"]
