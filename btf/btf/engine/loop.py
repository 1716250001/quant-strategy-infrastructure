# -*- coding: utf-8 -*-
"""引擎主循环：十步骤时序（06 §10.1；PoC-2 任务 2.5）。

步骤实现（每交易日 T）：
    ① 公司行动（PoC-3 任务 3.2：ex/pay 两时点分发——run 起点装载区间
       实施态行动并按 ex_date/pay_date 建索引；ex 调整在 advance_day 解冻
       之后、撮合之前：送转 qty/available_qty/avg_cost 同步调整、派息只
       摊成本并登记 qty_at_ex；pay 按登记数量入现金——清仓后仍发放，
       终审实现注意②；pay_date 缺失回退 ex 当日到账，E5）
    ② 撮合 T-1 订单队列（NextOpenHandler：拒单六 code + 逐单入账）
       注：④ 原文的"③ 更新 Position"在 execute 内逐单 apply_fill 实现
       （INSUFFICIENT_CASH 逐单扣减语义要求顺序入账）。
    ④ on_open(ctx, T)（撮合后，可读当日持仓现金）
    ⑤ 收盘重估 mark_all（持仓标的当日 close；②③④ 后、⑥ 前）
    ⑥ on_close(ctx, T)（默认决策点：submit_target / submit_order）
    ⑦ Rebalancer.generate_orders（目标 → 草稿订单，卖先买后）
    ⑧ 风控预检（M2 5.2 风控链；PoC 空链放行——默认降级策略，启动警告一次）
    ⑨ 订单入队（确定性赋号 O00000001…）+ OrderSubmittedEvent
    ⑩ SessionEnd 快照（N5-1 四式）+ SessionEndEvent

每交易日事件序（时序单测锚点，04 §8.4 循环事件投影）：
    CorporateActionEvent*（步骤①；仅持仓标的——无持仓 no-op 不入日志，
    全市场行动每日数百条，全发将冲垮 50 万事件预算；ex/pay 两时点各一条）
    → MarketOpenEvent → FillEvent*/OrderRejectedEvent* → MarketCloseEvent
    → OrderSubmittedEvent* → SessionEndEvent

T+1 时序：advance_day（解冻）在撮合前——昨日买入今日可卖；
当日买入当日卖由 handler T_PLUS_1 拒单兜底。

组装根职责（铁律 2 例外）：engine 是唯一 import execution/portfolio/
strategy 的组合根；三者互不依赖（各自以 Protocol 声明依赖面）。

性能（B2 依据）：每日循环只做 bisect 单行 Bar 构造（_LazyCrossSection
单键稳定排序等价性证据），不触发全截面物化（22s 禁区，B1 物化段证据）；
ctx states 惰性装载（LazyStates：不查不触发），实际查询限定（持仓∪订单）
标的子集且 with_touch_flags=False。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from btf.data.feed import DataFeed
from btf.domain.action import CorporateAction
from btf.domain.cache import BoundedDict
from btf.domain.events import (
    CorporateActionEvent,
    FillEvent,
    MarketCloseEvent,
    MarketOpenEvent,
    OrderRejectedEvent,
    OrderSubmittedEvent,
    SessionEndEvent,
)
from btf.domain.market import Bar
from btf.domain.orders import Fill, Order, RejectCode, Rejection
from btf.domain.types import Instrument, TradingDate
from btf.engine.clock import SimClock
from btf.engine.events_log import EventLogWriter
from btf.execution.handler import NextOpenHandler
from btf.portfolio.portfolio import Portfolio, PortfolioSnapshot
from btf.portfolio.rebalancer import FullRebalancer
from btf.risk.manager import RuleChainManager
from btf.strategy.base import StrategyBase
from btf.strategy.context import LazyStates, StrategyContext
from btf.strategy.rebalance import TargetPortfolio

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """回测结果（PoC 简版；M2 5.5 RunStore 负责目录化/指纹/归档）。"""

    snapshots: list[PortfolioSnapshot] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    rejections: list[tuple[Order, Rejection]] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)     # 全部已提交订单（含被拒）
    n_days: int = 0
    events_path: Path | None = None
    events_sampled: bool = False                          # 事件日志采样降级标志


class Engine:
    """事件循环引擎（回测/实时同内核，06 §10.2——仅时钟与 Feed 不同）。"""

    def __init__(
        self,
        feed: DataFeed,
        strategy: StrategyBase,
        *,
        start: TradingDate,
        end: TradingDate,
        initial_cash: float = 1_000_000.0,
        handler: NextOpenHandler | None = None,
        rebalancer: FullRebalancer | None = None,
        risk_manager: RuleChainManager | None = None,
        instruments: Mapping | None = None,
        cross_section: Sequence[str] | None = None,
        event_log_path: Path | str | None = None,
        event_log_max_records: int = 500_000,
    ):
        self._feed = feed
        self._strategy = strategy
        self._start = start
        self._end = end
        self._initial_cash = initial_cash
        self._instruments = dict(instruments or {})
        #: 截面标的限制（V3-1）：混合表 Feed 无「全市场」语义，须显式给出
        #: 交易宇宙；None = 全市场（单表原型既有行为，不改）。
        self._cross_section = list(cross_section) if cross_section else None
        self._handler = handler or NextOpenHandler(instruments=self._instruments)
        self._rebalancer = rebalancer or FullRebalancer(self._instruments)
        self._risk = risk_manager
        self._event_log_path = Path(event_log_path) if event_log_path else None
        self._event_log_max = event_log_max_records

    def _universe_loader(self, date: TradingDate) -> list[Instrument]:
        """当日可交易宇宙（`ctx.universe()`；04 §8.3.2 静态身份）。

        口径（v0.5.2 修正，19 号 §18.2 X-5 连带缺陷）：引擎显式
        `instruments` 时**以其为宇宙**——与截面 `cross_section`（runtime 取
        `sorted(instruments)`）**同源**；否则回落 `feed.universe(date)`。

        原实现恒用 `feed.universe(date)`：子集宇宙场景（配置
        `universe.source=explicit|index`）下策略经 `ctx.universe()` 看到的
        是全市场，而截面只含配置标的 → 策略选出的标的不在截面 → 再平衡
        **静默零订单**（`examples/config_rotation.yaml` 实测 728 日 0 成交
        0 拒单；此前因配置死键被 config-check 拦下而未暴露）。
        """
        if self._instruments:
            return list(self._instruments.values())
        return list(self._feed.universe(date))

    # ── 主循环 ──
    def run(self) -> RunResult:
        clock = SimClock()
        portfolio = Portfolio(cash=self._initial_cash)
        result = RunResult()
        writer = (EventLogWriter(self._event_log_path, max_records=self._event_log_max)
                  if self._event_log_path else None)
        pending: list[Order] = []
        order_seq = 0
        # PF-9/P2-5：原无界 dict（键=策略查询过的标的；全市场口径可累积到
        # 全部标的 × 十年 Bar → 内存无上限）；今 LRU 有界（容量 ≫ 典型宇宙）。
        history_cache: BoundedDict[str, Sequence[Bar]] = BoundedDict(
            maxsize=8_000, name="engine.history")
        target: TargetPortfolio | None = None
        explicit_orders: list[Order] = []
        warned_risk_chain = False

        # 步骤①数据源：区间实施态行动一次性装载（升序 (ex_date, symbol)
        # =确定性；dividend 表小 ~0.2s/10 年，B2 预算内）+ 两时点索引。
        # by_pay 仅收录 ex≠pay 的行动（pay≡ex 在 ex 分支当日到账）。
        # 行动编号 idx 作 pending 登记键——同标的同日多行动（如 600811
        # 19960730 送转/派息拆 3 行）不会互相覆盖丢分红。
        actions_by_ex: dict[str, list[tuple[int, CorporateAction]]] = {}
        actions_by_pay: dict[str, list[tuple[int, CorporateAction]]] = {}
        for idx, act in enumerate(self._feed.corporate_actions(self._start, self._end)):
            actions_by_ex.setdefault(act.ex_date.to_ymd(), []).append((idx, act))
            if act.pay_date is not None and act.pay_date != act.ex_date:
                actions_by_pay.setdefault(act.pay_date.to_ymd(), []).append((idx, act))
        # qty_at_ex 跨窗口登记（实现注意②）：idx → (qty_at_ex, cash_div)
        pending_divs: dict[int, tuple[int, float]] = {}

        def _history_loader(symbol: str) -> Sequence[Bar]:
            bars = history_cache.get(symbol)
            if bars is None:
                bars = self._feed.bars_of(symbol, self._start, self._end)
                history_cache[symbol] = bars
            return bars

        def _on_submit_target(t: TargetPortfolio) -> None:
            nonlocal target
            target = t

        def _on_submit_order(o: Order) -> None:
            explicit_orders.append(o)

        def _emit(event) -> None:
            if writer is not None:
                writer.write(event)

        # 循环前 init（空数据 ctx——预计算/缓存装载语义）
        ctx = self._make_ctx(clock, portfolio, {}, {}, _history_loader,
                             _on_submit_target, _on_submit_order)
        self._strategy.init(ctx)

        for date, data in self._feed.bars(self._cross_section,
                                          self._start, self._end):
            clock.advance(date)
            portfolio.advance_day()        # T+1 解冻（撮合前——昨日买入今日可卖）

            # ① 公司行动（两时点；advance_day 解冻后——送转扩张发生在
            #    available_qty=qty 基础上，除权次日可卖不被低估，实现注意①）
            ymd = date.to_ymd()
            for idx, act in actions_by_ex.get(ymd, ()):
                if act.symbol not in portfolio.positions:
                    continue           # 无持仓 no-op（不入事件日志）
                qty_at_ex = portfolio.apply_ex_date(act)
                _emit(CorporateActionEvent(action=act))        # ex 时点
                if act.cash_div_per_share > 0:
                    eff_pay = act.pay_date or act.ex_date      # E5 回退
                    if eff_pay == date:                        # 两日重合/回退
                        if qty_at_ex:
                            portfolio.apply_pay_date(
                                act.symbol, qty_at_ex, act.cash_div_per_share)
                            _emit(CorporateActionEvent(action=act))  # pay 时点
                    else:
                        pending_divs[idx] = (qty_at_ex, act.cash_div_per_share)
            for idx, act in actions_by_pay.get(ymd, ()):       # 跨日 pay 到期
                reg = pending_divs.pop(idx, None)
                if reg is not None:
                    qty_at_ex, cash_div = reg
                    portfolio.apply_pay_date(act.symbol, qty_at_ex, cash_div)
                    _emit(CorporateActionEvent(action=act))    # pay 时点

            # ② 撮合 T-1 队列（③ 入账在 execute 内逐单）
            _emit(MarketOpenEvent(date=date, data=data))
            if pending:
                involved = sorted({o.symbol for o in pending})
                states = self._feed.trading_states(
                    date, involved, with_touch_flags=False)
                fills, rejections = self._handler.execute(
                    pending, date, data, states, portfolio)
                for f in fills:
                    result.fills.append(f)
                    _emit(FillEvent(fill=f))
                for o, r in rejections:
                    result.rejections.append((o, r))
                    _emit(OrderRejectedEvent(order=o, reason=r))
            pending = []

            # ④ on_open（撮合后；ctx.states 覆盖**持仓**标的——pending 已在
            #    ② 段消费并清空）
            #    P3-1（19 号附录 D.3，**死代码清理**）：原表达式写
            #    `set(portfolio.positions) | {o.symbol for o in pending}`，
            #    而此刻 `pending` 已在上方清空（`pending = []`）——并集右项
            #    恒为空集，且下游 `LazyStates` 的标的集合在**构造时点固化**
            #    （后续变异不影响），故原式的"看起来覆盖队列标的"是误导性
            #    死代码。今显式只取持仓（语义与运行时行为逐位一致）。
            #    B2 优化（第二刀）：states 惰性装载——策略不查状态则不触发
            #    feed 查询（ST 区间扫描 + TradingState 合成全豁免）。
            _date, _symbols = date, sorted(portfolio.positions)
            ctx = self._make_ctx(
                clock, portfolio, data,
                LazyStates(lambda d=_date, s=_symbols: self._feed.trading_states(
                    d, s, with_touch_flags=False)),
                _history_loader, _on_submit_target, _on_submit_order)
            self._strategy.on_open(ctx, date)

            # ⑤ 收盘重估（持仓标的）
            # B3 十年尺度第二刀（CP1「快照字段物化削减」）：mark 只需 close
            # 一列——列式截面提供 `closes()` 轻量视图（免 ~8M Bar/十年）；
            # MemoryFeed 截面为 dict[Bar] 无此方法 → 逐持仓取用（语义不变）。
            day_closes = getattr(data, "closes", None)
            if day_closes is not None:
                # 直接传持仓集合（PF-3：免全截面 dict 建写，按需产出）
                closes = day_closes(portfolio.positions)
            else:
                closes = {}
                for sym in portfolio.positions:
                    bar = data.get(sym)
                    if bar is not None:
                        closes[sym] = bar.close
            portfolio.mark_all(closes)

            # ⑥ on_close（默认决策点）
            _emit(MarketCloseEvent(date=date, data=data))
            target = None
            explicit_orders = []
            self._strategy.on_close(ctx, date)

            # ⑦ 目标 → 订单（草稿）
            drafts: list[Order] = list(explicit_orders)
            if target is not None:
                drafts.extend(self._rebalancer.generate_orders(
                    target, portfolio,
                    self._feed.trading_states(
                        date, sorted(set(target.targets) | set(portfolio.positions)),
                        with_touch_flags=False),
                    data))

            # ⑧ 风控预检（M2 5.2：规则链逐单裁决——首 REDUCE/REJECT 生效；
            # 否决转 Rejection(code=risk_rejected) 进当日拒单统计，06 §10.6）
            # states 覆盖 drafts 标的（涨跌停预拒需 touch flags=True）；
            # LazyStates 惰性——链无 tradability 规则时不触发查询。
            # 盯市补充：drafts 标的（可能未持仓——首日建仓）先以当日收盘
            # 盯市，供 close_of 预估（无价规则会降级放行）。
            if drafts and self._risk is not None:
                mark = {o.symbol: data[o.symbol].close
                        for o in drafts if o.symbol in data}
                if mark:
                    portfolio.mark_all(mark)
                syms = sorted({o.symbol for o in drafts})
                risk_states = LazyStates(
                    lambda d=date, s=syms:
                        self._feed.trading_states(d, s, with_touch_flags=True))
                drafts = self._risk.pre_check(drafts, portfolio, risk_states)
                for order, reason in self._risk.batch_rejections:
                    result.rejections.append(
                        (order, Rejection(RejectCode.RISK_REJECTED,
                                          f"{reason}（风控预检）")))
                    result.orders.append(order)   # 被拒也计入已提交（审计）
                    _emit(OrderRejectedEvent(
                        order=order,
                        reason=Rejection(RejectCode.RISK_REJECTED,
                                         f"{reason}（风控预检）")))
            elif drafts and not warned_risk_chain:
                logger.warning("风控链为空：订单直接入队（04 §8.5 扩展点 6 降级）")
                warned_risk_chain = True

            # ⑨ 确定性赋号 + 入队
            for draft in drafts:
                order_seq += 1
                order = replace(draft, order_id=f"O{order_seq:08d}")
                result.orders.append(order)
                pending.append(order)
                _emit(OrderSubmittedEvent(order=order))

            # ⑩ 快照 + SessionEnd
            result.snapshots.append(portfolio.snapshot(date))
            _emit(SessionEndEvent(date=date))

        result.n_days = clock.n_days
        if writer is not None:
            writer.close()
            result.events_path = writer.path
            result.events_sampled = writer.sampled
        return result

    # ── ctx 工厂 ──
    def _make_ctx(self, clock, portfolio, data, states, loader, on_target, on_order):
        return StrategyContext(
            now=lambda: clock.current,
            portfolio=portfolio,
            data=data,
            states=states,
            history_loader=loader,
            on_submit_target=on_target,
            on_submit_order=on_order,
            start=self._start,
            end=self._end,
            universe_loader=self._universe_loader,
        )


__all__ = ["Engine", "RunResult"]
