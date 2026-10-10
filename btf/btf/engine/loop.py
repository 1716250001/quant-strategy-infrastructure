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

import inspect
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from btf.data.feed import DataFeed
from btf.data.tables_meta import daily_table_of
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
from btf.domain.orders import Fill, Order, OrderSide, RejectCode, Rejection
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
    #: RC-11-A（2026-10-05）：**退市持仓残值披露**——区间末仍持仓且已退市的
    #: 标的：`[{symbol, last_trade_date}]`。退市后无 bar ⇒ 市值沿用末次收盘价
    #: （`portfolio.py` 残值携带语义，**数值零改动**，黄金集断言不受影响）；
    #: 由 runtime 转成装配披露（manifest + 报告「假设与披露」可见）。
    delisted_holdings: list[dict[str, str]] = field(default_factory=list)
    #: **CL-3（19 号 §72.4）**：退市清算口径——`carry`（默认＝现状：残值按区间内
    #: 末次可用收盘价携带、未清算）/ `writeoff`（按 0 核销，压力口径）。
    delisted_settlement: str = "carry"
    #: writeoff 的核销额（元；0 = 未核销/无退市持仓）——披露与手算校验锚点。
    delisted_writeoff_amount: float = 0.0
    #: **CL-2 第二段（R5′，19 号 §79.4）**：配股行权审计（逐事件：获配股数 /
    #: 缴款额 / 现金缺口 / 理论除权价 / 上市日）——runtime 转 run 元数据与披露。
    rights_events: list[dict[str, Any]] = field(default_factory=list)
    #: 缩股（R6′ 代理）审计。
    consolidations: list[dict[str, Any]] = field(default_factory=list)
    #: **CL-2-H（R32′；ADR-20 第 2 条）**：派息现金到账审计（逐事件：标的 /
    #: 除息日 / 到账日 / `qty_at_ex` / 每份金额 / 到账额）——覆盖段与归因消费。
    dividend_events: list[dict[str, Any]] = field(default_factory=list)
    #: **R62′（N64）**：消费侧价格视图（`run.price_view`；见 `Engine.__init__`）。
    #: 只改「喂给策略的 bar」——记账侧（撮合/成本/费用/NAV/快照）恒名义价。
    price_view: str = "nominal"
    #: 复权基准日（`adjusted` 时＝区间起点；`nominal` 时 None）。
    price_view_ref: TradingDate | None = None
    #: 复权降级披露（因子缺失回退 1.0 等；并入装配说明/报告「假设与披露」）。
    adjust_notes: tuple[str, ...] = ()
    #: 配股锁定期内卖出警告（获配股份上市日前卖出；**默认不建模锁定期**，
    #: 故只警告、不阻断——§79.3 #3）。
    lockup_sell_warnings: list[str] = field(default_factory=list)


def _writeoff_settlement(portfolio: Portfolio, result: RunResult) -> float:
    """`writeoff`：区间末按 0 核销退市持仓残值（**CL-3**，19 号 §72.4）。

    口径出处：Q-1(a)＝清算价取「退市整理期末交易日收盘」＝ 区间内**末次可用
    收盘价**（`_last_close`）；Q-2(b)＝默认现状 ＋ `--delisted-settlement` 显式
    选择。**防前视**（§72.4 注解 3）：核销基准一律取 ≤ 区间末的末次收盘，
    **不取任何区间外价**。

    语义（只作用于**末日快照**，此前路径逐位不变）：
      - 该标的市场价值 / 权重清零、`positions_qty` 置 0（"持仓视同清零"）；
      - NAV 下调核销额；`daily_return / cumulative_return / drawdown` 按
        N5-1 四式**重算**（保持 `audit_nav_four` 零漂移）；
      - 引擎持仓对象 / 成交 / 事件日志**不改**（无成交、无现金流——纯估值口径）。

    返回核销额（元）。
    """
    residual = 0.0
    syms: list[str] = []
    for item in result.delisted_holdings:
        sym = str((item or {}).get("symbol"))
        pos = portfolio.positions.get(sym)
        if pos is None or not pos.qty:
            continue
        syms.append(sym)
        residual += pos.qty * portfolio._last_close.get(sym, pos.avg_cost)
    if not syms or not residual:
        return 0.0

    snapshots = result.snapshots
    last = snapshots[-1]
    new_tv = last.total_value - residual
    base = snapshots[0].total_value
    prev_tv = snapshots[-2].total_value if len(snapshots) > 1 else None
    peak = max((s.total_value for s in snapshots[:-1]), default=new_tv)
    peak = max(peak, new_tv)
    scale = (last.total_value / new_tv) if new_tv else 0.0
    drop = set(syms)
    snapshots[-1] = replace(
        last,
        market_value=last.market_value - residual,
        total_value=new_tv,
        weights={k: v * scale for k, v in last.weights.items() if k not in drop},
        positions_qty={k: v for k, v in last.positions_qty.items()
                       if k not in drop},
        daily_return=(0.0 if not prev_tv else new_tv / prev_tv - 1.0),
        cumulative_return=(0.0 if not base else new_tv / base - 1.0),
        drawdown=(0.0 if not peak else new_tv / peak - 1.0),
    )
    return residual


class _AdjustedDataSource(Mapping[str, Bar]):
    """**消费侧复权视图**（R62′/N64；19 号 §118）：`ctx.bar()` 读到的当日 bar。

    只包装 `ctx` 的数据入口——**记账/撮合/重估/再平衡一律用原始截面**（ADR-6）。
    惰性逐标的换算 + 当日 memo（同一 bar 只换一次）。
    """

    __slots__ = ("_adjust", "_cache", "_raw")

    def __init__(self, raw: Mapping[str, Bar], adjust: Any):
        self._raw = raw
        self._adjust = adjust
        self._cache: dict[str, Bar | None] = {}

    def _one(self, symbol: str) -> Bar | None:
        if symbol in self._cache:
            return self._cache[symbol]
        bar = self._raw.get(symbol)
        out = None if bar is None else self._adjust([bar])[0]
        self._cache[symbol] = out
        return out

    def get(self, symbol: str, default: Any = None) -> Any:
        bar = self._one(symbol)
        return default if bar is None else bar

    def __getitem__(self, symbol: str) -> Bar:
        bar = self._one(symbol)
        if bar is None:
            raise KeyError(symbol)
        return bar

    def __iter__(self):
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)


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
        delisted_settlement: str = "carry",
        price_view: str = "adjusted",
        adjust_service: Any | None = None,
    ):
        if delisted_settlement not in ("carry", "writeoff"):
            raise ValueError(
                f"delisted_settlement 须为 carry|writeoff，得"
                f" {delisted_settlement!r}（CL-3 / 19 号 §72.4）")
        if price_view not in ("nominal", "adjusted"):
            raise ValueError(
                f"price_view 须为 nominal|adjusted，得 {price_view!r}"
                f"（R62′/N64；19 号 §118）")
        self._delisted_settlement = delisted_settlement
        #: **R62′（N64，2026-10-10；19 号 §118）——消费侧价格视图**：
        #: `adjusted`（默认）＝喂给策略的 bar（`ctx.bar()` / `ctx.history()`）走
        #: **qfq 后复权**，ref＝**区间起点**（PIT 合规：qfq(t) 只依赖 ≤t 的因子；
        #: hfq 依赖全序列 ⇒ **禁用**，§118.2）；`nominal` ＝ 名义价（旧行为），
        #: 供对照实验与回归比对。**记账侧恒名义价**（ADR-6 不动）：撮合价/持仓
        #: 成本/费用/NAV/snapshots 全部仍由名义价计算——本视图只包装
        #: `ctx` 的两个数据入口，引擎内其他消费点（撮合/重估/再平衡）不看它。
        self._price_view = price_view
        #: 注入点（测试用；None ⇒ 惰性构造 `TushareAdjustService`）。
        self._adjust_service = adjust_service
        #: 运行期填充（降级披露 + 基准日）。
        self._price_view_ref: TradingDate | None = None
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

    # ── 消费侧价格视图（R62′/N64）──
    def _build_price_adjuster(self) -> Any | None:
        """构造 qfq 换算闭包（`nominal` ⇒ None＝不调整）。

        - **公式/基准（§118.2/§118.3-2）**：`qfq(t) = price(t)·f(t)/f(ref)`，
          `ref = self._start`（区间起点；`adj_factor(ref)` 取 **≤ref 的最近因子**
          ⇒ 与「period.start 的首个交易日」数值等价，除权日必为交易日）。
          **禁止 hfq**（依赖全序列因子 ⇒ 前视污染）。
        - **数据源（§118.3-3）**：`TushareAdjustService`（本地主库，
          `factor_table_of` 路由：股票 `adj_factor` / ETF·LOF `fund_adj`）——
          实测非网络调用；**指数**无复权语义（`index_daily`）⇒ 原样返回。
        - **降级（§118.3-4）**：因子缺失 ⇒ 服务内回退 1.0 并计数，
          经 `degraded_notes()` 进装配说明/报告披露（禁静默）。
        """
        if self._price_view != "adjusted":
            return None
        if self._adjust_service is None:
            from btf.data.adjust import TushareAdjustService

            self._adjust_service = TushareAdjustService()
        svc = self._adjust_service
        ref = self._start
        self._price_view_ref = ref

        def _adjust(bars: Sequence[Bar]) -> list[Bar]:
            if not bars:
                return list(bars)
            if daily_table_of(bars[0].symbol) == "index_daily":
                return list(bars)          # 指数：无复权因子（btf 内不复权指数）
            return list(svc.qfq(bars, ref))

        return _adjust

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
        price_adjust = self._build_price_adjuster()   # R62′：None ⇒ nominal（旧行为）
        clock = SimClock()
        portfolio = Portfolio(cash=self._initial_cash)
        result = RunResult()
        writer = (EventLogWriter(self._event_log_path, max_records=self._event_log_max)
                  if self._event_log_path else None)
        pending: list[Order] = []
        order_seq = 0
        #: RC-11-A：持仓标的的末次有 bar 日（退市持仓披露用）
        last_seen: dict[str, TradingDate] = {}
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
        # **CL-2-H（R32′）**：显式宇宙（`_cross_section`）给出时随装载一并传入
        # ——ETF/LOF 公司行动按宇宙内 `is_fund_code` 标的上限装载（避免全 ETF
        # 扫描）；**签名探测**兼容未扩参的自定义/Memory Feed（无 `symbols`
        # 形参 ⇒ 原两参调用，语义不变）。
        _ca = self._feed.corporate_actions
        if "symbols" in inspect.signature(_ca).parameters:
            _ca_iter = _ca(self._start, self._end, symbols=self._cross_section)
        else:
            _ca_iter = _ca(self._start, self._end)
        for idx, act in enumerate(_ca_iter):
            actions_by_ex.setdefault(act.ex_date.to_ymd(), []).append((idx, act))
            if act.pay_date is not None and act.pay_date != act.ex_date:
                actions_by_pay.setdefault(act.pay_date.to_ymd(), []).append((idx, act))
        # qty_at_ex 跨窗口登记（实现注意②）：idx → (qty_at_ex, cash_div)
        pending_divs: dict[int, tuple[int, float]] = {}

        def _history_loader(symbol: str) -> Sequence[Bar]:
            bars = history_cache.get(symbol)
            if bars is None:
                bars = self._feed.bars_of(symbol, self._start, self._end)
                if price_adjust is not None:
                    # R62′：`ctx.history()` 同走消费侧复权（一次换算全区间 ⇒ 缓存即调整后序列）
                    bars = price_adjust(bars)
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
                            portfolio.dividend_events.append({
                                "symbol": act.symbol,
                                "ex_date": act.ex_date.to_ymd(),
                                "pay_date": ymd,
                                "qty_at_ex": int(qty_at_ex),
                                "cash_div": act.cash_div_per_share,
                                "amount": qty_at_ex * act.cash_div_per_share,
                            })
                            _emit(CorporateActionEvent(action=act))  # pay 时点
                    else:
                        pending_divs[idx] = (qty_at_ex, act.cash_div_per_share)
            for idx, act in actions_by_pay.get(ymd, ()):       # 跨日 pay 到期
                reg = pending_divs.pop(idx, None)
                if reg is not None:
                    qty_at_ex, cash_div = reg
                    portfolio.apply_pay_date(act.symbol, qty_at_ex, cash_div)
                    portfolio.dividend_events.append({
                        "symbol": act.symbol,
                        "ex_date": act.ex_date.to_ymd(),
                        "pay_date": ymd,
                        "qty_at_ex": int(qty_at_ex),
                        "cash_div": cash_div,
                        "amount": qty_at_ex * cash_div,
                    })
                    _emit(CorporateActionEvent(action=act))    # pay 时点

            # ② 撮合 T-1 队列（③ 入账在 execute 内逐单）
            _emit(MarketOpenEvent(date=date, data=data))
            if pending:
                # RC-10（2026-10-05）：**引擎级双时间戳断言**——信号日必须严格
                # 早于执行日（`signal_time < execution_time`）。当前唯一注册
                # 撮合路径是 next_open（T 收盘决策 → T+1 开盘成交），本断言是
                # 对"将来新增同日/盘中撮合路径"的结构性防线（D 篇机械规则三）：
                # 违反 ⇒ fail-closed，绝不静默按当日价成交。
                for _o in pending:
                    if not _o.created_at < date:
                        raise RuntimeError(
                            f"双时间戳断言失败：{_o.order_id} signal_time="
                            f"{_o.created_at.to_ymd()} ≥ execution_time="
                            f"{date.to_ymd()}——信号不得当日成交（RC-10，"
                            f"撮合仅 next_open：T 决策 → T+1 开盘执行）")
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
            # **R62′（N64）**：ctx 的当日截面走消费侧复权视图（`nominal` ⇒ 原样）；
            # 循环内其余用途（撮合/重估/再平衡）**仍用原始 `data`**（ADR-6）。
            ctx_data = (data if price_adjust is None
                        else _AdjustedDataSource(data, price_adjust))
            ctx = self._make_ctx(
                clock, portfolio, ctx_data,
                LazyStates(lambda d=_date, s=_symbols: self._feed.trading_states(
                    d, s, with_touch_flags=False)),
                _history_loader, _on_submit_target, _on_submit_order)
            # RC-10：on_open 阶段——当日 bar 未收盘，ctx.bar() 拒访、
            # history(end ≥ 当日) 拒（前视封堵；状态面板/持仓仍可读）
            ctx._set_phase("open")
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
            # RC-11-A：登记持仓标的的**末次有 bar 日**（退市持仓披露用；
            # 无 bar 的日子不更新——末日仍缺 bar 且状态=退市 ⇒ 残值携带）
            for _held in closes:
                last_seen[_held] = date

            # ⑥ on_close（默认决策点）
            _emit(MarketCloseEvent(date=date, data=data))
            target = None
            explicit_orders = []
            # RC-10：切回 close 阶段——收盘价已产生（⑤ 已重估），
            # ctx.bar() 与 history(end == 当日) 恢复合法（既有契约）
            ctx._set_phase("close")
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

        # RC-11-A（2026-10-05）：退市持仓残值披露——**只披露、不改数**。
        # 候选 = 区间末仍持仓（qty>0）且末日无 bar 的标的；对候选做一次
        # 状态查询，`is_delisted=True` 命中 ⇒ 登记（标的 / 末次交易日）。
        # 提示：停牌（非退市）末日无 bar 的持仓**不进**本清单（反向探针依赖）。
        if result.snapshots:
            end_day = result.snapshots[-1].date
            held = [s for s, pos in portfolio.positions.items() if pos.qty]
            cands = sorted(s for s in held if last_seen.get(s) != end_day)
            if cands:
                end_states = self._feed.trading_states(
                    end_day, cands, with_touch_flags=False)
                for _sym in cands:
                    _st = end_states.get(_sym)
                    if _st is not None and _st.is_delisted:
                        result.delisted_holdings.append({
                            "symbol": _sym,
                            "last_trade_date": (last_seen[_sym].to_ymd()
                                                if _sym in last_seen else ""),
                        })
        # CL-3（19 号 §72.4；Q-2(b) 已裁）：清算口径登记 + writeoff 核销。
        # carry（默认）＝ 现状（RC-11-A 只披露不改数）；writeoff ＝ 压力档：
        # 区间末按 0 核销（末日快照清零、NAV 下调核销额；四式重算保一致）。
        result.delisted_settlement = self._delisted_settlement
        if (self._delisted_settlement == "writeoff"
                and result.delisted_holdings and result.snapshots):
            result.delisted_writeoff_amount = _writeoff_settlement(
                portfolio, result)
        # CL-2 第二段（R5′，19 号 §79.4）：审计转出 ＋ 锁定期卖出警告。
        # 披露纪律：现金缺口 / 锁定期内卖出**必须可观测**（§79.3 #2/#3），
        # 故 logger.warning（非静默）＋ 转 run 元数据（runtime 侧装配说明）。
        result.rights_events = list(portfolio.rights_events)
        result.consolidations = list(portfolio.consolidations)
        # CL-2-H（R32′；ADR-20 第 2 条）：派息现金到账审计转出（覆盖段消费）。
        result.dividend_events = list(portfolio.dividend_events)
        for _ev in result.rights_events:
            if _ev.get("cash_shortfall"):
                logger.warning(
                    "配股缴款现金缺口 %.2f 元（%s 除权 %s）——默认外部增资补足"
                    "（CL-2 / §79.3 #2；非静默）", _ev["cash_shortfall"],
                    _ev["symbol"], _ev["ex_date"])
        if result.rights_events and result.fills:
            for _ev in result.rights_events:
                _listing = _ev.get("listing_date")
                if not _listing:
                    continue
                for _f in result.fills:
                    if (_f.symbol == _ev["symbol"]
                            and _f.side is OrderSide.SELL
                            and _ev["ex_date"] < _f.fill_date.to_ymd()
                            < _listing):
                        _msg = (f"配股锁定期内卖出：{_ev['symbol']} 于 "
                                f"{_f.fill_date.to_ymd()}（除权 "
                                f"{_ev['ex_date']} → 获配上市 {_listing}；"
                                f"默认**不建模锁定期**——CL-2 / §79.3 #3）")
                        if _msg not in result.lockup_sell_warnings:
                            result.lockup_sell_warnings.append(_msg)
                            logger.warning(_msg)
        result.n_days = clock.n_days
        # **R62′（N64）**：价格视图元数据 + 复权降级披露（runtime 转装配说明）
        result.price_view = self._price_view
        result.price_view_ref = self._price_view_ref
        if self._price_view == "adjusted" and self._adjust_service is not None:
            _dn = getattr(self._adjust_service, "degraded_notes", None)
            result.adjust_notes = tuple(_dn() if callable(_dn) else ())
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
