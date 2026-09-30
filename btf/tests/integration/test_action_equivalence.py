# -*- coding: utf-8 -*-
"""L2 集成测试：G9 数学等价断言（PoC-3 任务 3.5 验收）。

事件链累积收益率（理想分红再投资口径）≡ hfq 序列累积收益率
（11 §21.1；5 只全历史真实数据，引擎完整跑 buy-and-hold）。

数学内核（推导留痕）：
    再投资账本不变式 V_tilde(t) = qty_tilde(t)×close(t)，行动日按
    "分红现金按除权后基准转份额"重定标：
        qty_tilde ×= (1+sd) × p/(p−cd)     p=除权前昨收
    → V_tilde 日收益率 = (1+sd)×close(t)/(p−cd) = close(t)/pre_close(t)
      （A 股除权定价公式回代）= r_hfq(t)（hfq 连续性恒等式）
    → 累积恒等（残差=除权价 2 位小数舍入，35 年 ~30 次行动 <1%）。

三层断言（"断言=独立手算"纪律）：
    1 引擎份额链 == 持现账本逐行动重放（round 同公式，精确相等）
    2 引擎现金链 == 1e6 − 买入成本 + Σ qty_at_ex×cd（同序累加，精确）
    3 理想再投资账本累积收益 ≡ hfq 累积收益（容差 1%）

选样：dividend 实施态送转次数 Top5（600276.SH 18 次，
000001.SZ / 000002.SZ / 600811.SH / 600867.SH 各 14 次）——
行动密度最大化路径覆盖（含同日多行动/停牌中除权/90 年代 ex≠pay）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.adjust import TushareAdjustService
from btf.data.feed import TushareParquetFeed
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.engine.loop import Engine
from btf.execution.cost import ZeroCostModel
from btf.execution.handler import NextOpenHandler
from btf.strategy.base import StrategyBase
from btf.strategy.context import StrategyContext
from btf.strategy.rebalance import TargetPortfolio

pytestmark = [pytest.mark.l2]

ROOT = Path(MARKET_DATA_DIR)
SYMBOLS = ["600276.SH", "000001.SZ", "000002.SZ", "600811.SH", "600867.SH"]
# 窗口=三表覆盖交集：adj_factor/stk_limit 均自 2008 年起有行（撮合路径
# 状态合成依赖 stk_limit；恒等式基准依赖 adj_factor——1991-2007 不可比，
# 600811 af 止于 2025-08-14 退市整理，末段前向填充自洽）。17 年窗口
# 每只 6-12 次行动，路径密度保留。
START = TradingDate.from_ymd("20080102")
END = TradingDate.from_ymd("20251231")
INITIAL_CASH = 1_000_000.0
G9_TOL = 0.01          # 1%：除权价 2 位小数舍入累积 + af 舍入噪声


class FirstBarBuyHold(StrategyBase):
    """首个有 bar 收盘全仓买入，持有到期末（buy-and-hold 基准）。"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.done = False

    def on_close(self, ctx: StrategyContext, date: TradingDate) -> None:
        if not self.done and ctx.history(self.symbol, date, n_bars=1):
            ctx.submit_target(
                TargetPortfolio(rebalance_date=date, targets={self.symbol: 1.0}))
            self.done = True


def _skip_if_no_data():
    if not (ROOT / "dividend").is_dir():
        pytest.skip("主库 dividend/adj_factor 不可用")


@pytest.fixture(scope="module")
def feed():
    _skip_if_no_data()
    # R4（19 号 §48）：引擎路径需状态面板 → 构造期注入（feed 不再 import state）
    from btf.data.state import StateSynthesizer

    return TushareParquetFeed(ROOT, state_provider=StateSynthesizer(ROOT))


@pytest.fixture(scope="module")
def adjust():
    _skip_if_no_data()
    return TushareAdjustService(ROOT)


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_g9_event_chain_equals_hfq(feed, adjust, symbol, tmp_path):
    bars = feed.bars_of(symbol, START, END)
    assert len(bars) > 1_000                       # 窗口量级护栏
    acts = [a for a in feed.corporate_actions(START, END) if a.symbol == symbol]
    assert len(acts) >= 6                          # 行动密度护栏（Top5）
    # ── 引擎世界：buy-and-hold 全历史 ──
    inst = {symbol: Instrument(symbol=symbol, asset_class=AssetClass.STOCK,
                               board="main", lot_size=100)}
    result = Engine(
        feed, FirstBarBuyHold(symbol), start=START, end=END,
        initial_cash=INITIAL_CASH,
        handler=NextOpenHandler(cost_model=ZeroCostModel(), instruments=inst),
        instruments=inst,
        event_log_path=tmp_path / "events.jsonl",
    ).run()
    assert len(result.fills) == 1 and not result.rejections
    buy = result.fills[0]
    qty0, price0 = buy.qty, buy.price

    # ── 独立账本重放（持现口径：引擎公式的第二实现）──
    # 行动按 ex 日分组（同日多行动逐行，feed (ex_date, symbol) 排序保序）
    acts_by_ex: dict[str, list] = {}
    for a in acts:
        acts_by_ex.setdefault(a.ex_date.to_ymd(), []).append(a)
    close_by_day = {b.date.to_ymd(): b.close for b in bars}
    day_seq = [s.date.to_ymd() for s in result.snapshots]   # 引擎日历同源
    fill_ymd = buy.fill_date.to_ymd()
    fill_i = day_seq.index(fill_ymd)

    qty_cash = qty0                       # 持现账本份额
    cash = INITIAL_CASH - qty0 * price0   # ZeroCost：无费用
    qty_tilde = float(qty0)               # 理想再投资账本份额
    v_tilde_base = None                   # 基准（成交日收盘）
    prev_close = None
    pending: list[tuple[str, int, float]] = []        # (pay_ymd, qty_at_ex, cd)

    for ymd in day_seq[fill_i:]:
        close = close_by_day.get(ymd, prev_close)      # 停牌前值语义（mark_all）
        if ymd > fill_ymd:                             # 成交日行动先于撮合，不适用
            for a in acts_by_ex.get(ymd, ()):          # 引擎同序逐行
                cd, sd = a.cash_div_per_share, a.stk_div_per_share
                # 持现账本：qty_at_ex=送转前登记 → 送转扩张
                qty_at_ex = qty_cash
                if sd:
                    qty_cash = round(qty_cash * (1 + sd))
                    qty_tilde *= 1 + sd
                if cd:
                    if prev_close is not None and prev_close > cd:
                        qty_tilde *= prev_close / (prev_close - cd)
                    eff_pay = (a.pay_date or a.ex_date).to_ymd()
                    if eff_pay == ymd:
                        cash += qty_at_ex * cd         # 当日到账/回退
                    else:
                        pending.append((eff_pay, qty_at_ex, cd))
        # pay 到期（跨日；pay 超窗留存=引擎 B1 口径不发放）
        rest = []
        for pay_ymd, q_at_ex, cd in pending:
            if pay_ymd == ymd:
                cash += q_at_ex * cd
            else:
                rest.append((pay_ymd, q_at_ex, cd))
        pending = rest
        if close is not None:
            if v_tilde_base is None:
                v_tilde_base = qty_tilde * close       # 成交日收盘基准
            prev_close = close

    # ── 断言 1/2：引擎账本 == 持现账本（精确）──
    last = result.snapshots[-1]
    assert last.positions_qty.get(symbol) == qty_cash
    assert last.cash == pytest.approx(cash, abs=1e-6)

    # ── 断言 3：理想再投资累积收益 ≡ hfq 累积收益 ──
    last_close = close_by_day.get(day_seq[-1], prev_close)
    v_tilde_end = qty_tilde * last_close
    cum_events = v_tilde_end / v_tilde_base
    hfq_base = close_by_day[fill_ymd] * adjust.adj_factor(symbol, buy.fill_date)
    hfq_end = last_close * adjust.adj_factor(
        symbol, TradingDate.from_ymd(day_seq[-1]))
    cum_hfq = hfq_end / hfq_base
    assert cum_events == pytest.approx(cum_hfq, rel=G9_TOL), (
        f"{symbol}: 事件链 {cum_events:.4f} vs hfq {cum_hfq:.4f}")
