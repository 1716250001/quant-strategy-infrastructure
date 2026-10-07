# -*- coding: utf-8 -*-
"""L2 集成：**CL-2 第二段**端到端（内存 Feed 注入配股事件；19 号 §79.4 R4′/R5′）。

覆盖：
  ① 配股事件 ⇒ `RunResult.rights_events` ＋ `assembly_notes` 逐事件披露
     （获配/缴款/上市日）＋ 清单状态行；
  ② **锁定期内卖出** ⇒ 警告（`lockup_sell_warnings` ＋ 披露，§79.3 #3）；
  ③ 反向探针：无事件的 run ⇒ **不得**出现「配股行权披露」（防恒现），
     但仍给清单状态行（禁静默）。
"""
from __future__ import annotations

import pytest
from btf import registry
from btf.data.memory import MemoryFeed
from btf.domain.action import CorporateAction
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.types import AssetClass, Instrument, TradingDate
from btf.runtime import BTFRuntime
from btf.strategy.base import StrategyBase
from btf.strategy.rebalance import TargetPortfolio

from tests.fixtures.scenarios import make_bar, make_state

pytestmark = [pytest.mark.l2]

FEED_RIGHTS = "cl2b_rights_feed"
FEED_PLAIN = "cl2b_plain_feed"
SYM = "600030.SH"
#: 前置一日（DPRE）用于建仓：引擎 T+1 next_open ⇒ DPRE 收盘信号在 D0 开盘成交
#: ⇒ **D0 收盘在册**（登记日持仓）；D1 为除权日（步骤① 时持仓已在）。
DPRE = TradingDate.from_iso(2015, 1, 2)
D0, D1, D2, D3, D4 = (TradingDate.from_iso(2015, 1, d)
                      for d in (5, 6, 7, 8, 9))
PX = {DPRE: 25.60, D0: 25.70, D1: 24.15, D2: 24.20, D3: 24.30, D4: 24.40}


class BuyThenMaybeSell(StrategyBase):
    """首日买入 90%；`sell_day` 当日清仓（用于构造锁定期内卖出）。"""

    contract_version = CONTRACT_VERSION

    def __init__(self, symbol: str = SYM, sell_day: str = "", **_params):
        self.symbol = symbol
        self.sell_day = sell_day
        self._bought = False

    def on_close(self, ctx, date) -> None:
        if self.sell_day and date.to_ymd() == self.sell_day:
            ctx.submit_target(TargetPortfolio(date, {}))
            return
        if not self._bought and ctx.bar(self.symbol) is not None:
            ctx.submit_target(TargetPortfolio(date, {self.symbol: 0.9}))
            self._bought = True


def _make_feed(*, with_rights: bool, listing: TradingDate | None) -> MemoryFeed:
    bars, states = [], {}
    for d in (DPRE, D0, D1, D2, D3, D4):
        bars.append(make_bar(SYM, d, PX[d], PX[d], PX[d], PX[d], PX[d]))
        states[(d.to_ymd(), SYM)] = make_state(SYM, d, pre=PX[d])
    instruments = {SYM: Instrument(SYM, AssetClass.STOCK, board="main",
                                   lot_size=100)}
    actions = []
    if with_rights:
        actions.append(CorporateAction(
            symbol=SYM, ex_date=D1, pay_date=None, record_date=D0,
            cash_div_per_share=0.0, stk_div_per_share=0.0, ann_date=None,
            rights_ratio=0.15, rights_price=14.43, rights_ex_date=D1,
            rights_listing_date=listing))
    return MemoryFeed(bars, states, instruments,
                      dates=[DPRE, D0, D1, D2, D3, D4], actions=actions)


@pytest.fixture(scope="module", autouse=True)
def _register_feeds():
    for name, feed in ((FEED_RIGHTS, _make_feed(with_rights=True, listing=D4)),
                       (FEED_PLAIN, _make_feed(with_rights=False, listing=None))):
        def factory(_feed=feed, **_params):
            return _feed

        factory.contract_version = CONTRACT_VERSION
        if name not in registry.available(registry.DATA_FEED):
            registry.register(registry.DATA_FEED, name, factory)


def _cfg(feed: str, *, sell_day: str = "") -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.integration.test_rights_cl2b:BuyThenMaybeSell",
            "params": {"symbol": SYM, "sell_day": sell_day},
            "universe": {"source": "explicit", "symbols": [SYM]},
            "period": {"start": "2015-01-02", "end": "2015-01-09"},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": feed},
        "risk": {"rules": [], "allow_empty_chain": True},
    }


class TestRightsEndToEnd:
    def test_rights_event_disclosed_and_booked(self):
        """① 事件入账（获配/缴款/上市日）＋ 逐事件披露 ＋ 清单状态行。"""
        rt = BTFRuntime().load_config(_cfg(FEED_RIGHTS)).build()
        result = rt.run(persist=False)

        assert len(result.rights_events) == 1
        ev = result.rights_events[0]
        # 建仓：DPRE 收盘按 25.60 定价 ⇒ 351 手 = 35100 股（D0 开盘成交）
        assert ev["qty_at_ex"] == 35100
        assert ev["rights_qty"] == int(35100 * 0.15)     # floor（§79.3 #1）
        assert ev["cost"] == pytest.approx(ev["rights_qty"] * 14.43, rel=1e-12)
        assert ev["listing_date"] == D4.to_ymd()
        assert ev["cash_shortfall"] == 0.0

        joined = " | ".join(rt.assembly_notes)
        assert "配股行权披露" in joined and SYM in joined
        assert str(ev["rights_qty"]) in joined and "上市日" in joined
        assert "配股/缩股清单" in joined                # 状态行（不恒空）

    def test_lockup_sell_warns_not_silent(self):
        """② 获配上市日前卖出 ⇒ 警告（不阻断、不静默）。"""
        rt = BTFRuntime().load_config(
            _cfg(FEED_RIGHTS, sell_day=D2.to_ymd())).build()
        result = rt.run(persist=False)
        assert result.lockup_sell_warnings, "锁定期内卖出未产生警告"
        assert any(D3.to_ymd() in w for w in result.lockup_sell_warnings)
        assert any("锁定期内卖出" in n for n in rt.assembly_notes)

    def test_plain_run_no_rights_disclosure(self):
        """③ 反向探针：无事件 ⇒ 不得出现逐事件披露；清单状态行仍在。"""
        rt = BTFRuntime().load_config(_cfg(FEED_PLAIN)).build()
        result = rt.run(persist=False)
        assert result.rights_events == [] and result.consolidations == []
        joined = " | ".join(rt.assembly_notes)
        assert "配股行权披露" not in joined
        assert "配股/缩股清单" in joined
        assert "未命中配股/缩股事件" in joined


class TestEventDayDecompositionG7:
    """R11′-G7（19 号 §81.2）：事件日分解 **ΔNAV ＝ M×(P − Pr)**，
    **P ＝ 除权日有效价**——有 bar ⇒ **真实收盘**；**不得**写死理论除权价
    `pex`（§80.5 对 B″-① 的订正：cb 实测 12.76 为真实收盘，非 Pex）。"""

    def _navs(self, feed: str):
        rt = BTFRuntime().load_config(_cfg(feed)).build()
        res = rt.run(persist=False)
        return ({s.date.to_ymd(): s.total_value for s in res.snapshots}, res)

    def test_event_day_delta_uses_real_close_not_pex(self):
        nav_on, res_on = self._navs(FEED_RIGHTS)
        nav_off, _ = self._navs(FEED_PLAIN)
        ev = res_on.rights_events[0]
        m, pr = ev["rights_qty"], 14.43          # floor(35100×0.15)=5265
        d1 = D1.to_ymd()
        delta = nav_on[d1] - nav_off[d1]
        # P ＝ D1 **真实收盘** 24.15（引擎 mark-to-market 用 bar 覆盖 pex）
        assert delta == pytest.approx(m * (PX[D1] - pr), rel=1e-12)
        # 反证：若写死 P＝pex（(25.70+0.15×14.43)/1.15≈24.230）则分解不成立
        pex = (PX[D0] + 0.15 * pr) / 1.15
        assert pex != pytest.approx(PX[D1], abs=1e-6)
        assert delta != pytest.approx(m * (pex - pr), rel=1e-3)
