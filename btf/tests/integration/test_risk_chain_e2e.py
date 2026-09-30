# -*- coding: utf-8 -*-
"""L4 集成：**真实风控链**下的端到端回测（19 号 §20.4.3 / Y-2）。

为什么本文件存在：X-7（Q1 改 A）把空风控链默认改为拒绝后，测试侧共 13 处
配置以 `allow_empty_chain: true` 显式声明"裸奔调试意图"（这是正确做法，且
**无一处在生产/样例配置**），副作用是：**绝大多数端到端用例实际跑在无风控
状态** → 风控路径（RuleChainManager → 规则裁决 → 引擎⑧ pre_check →
拒单/缩量入账）的端到端覆盖率显著下降。

本文件是该缺口的常驻补位：**在真实风控链下跑完整回测**，断言规则裁决真的
传导到了成交与拒单（而非只测规则单元）。

纪律（X-7）：本文件**不使用**逃生开关——它是"真实风控链"用例，空链/裸奔
语义由 `tests/unit/test_risk_rules.py` 与单规则单测覆盖。
"""
from __future__ import annotations

import pytest
from btf import registry
from btf.domain.contracts import CONTRACT_VERSION
from btf.domain.orders import RejectCode

pytestmark = [pytest.mark.l4]

FEED_NAME = "risk_e2e_feed"
PERIOD = {"start": "2015-01-05", "end": "2015-01-09"}
SYMBOL = "000001.SZ"


@pytest.fixture()
def _feed_registered():
    from tests.fixtures.scenarios import t_plus1_scenario

    feed = t_plus1_scenario().feed

    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if FEED_NAME not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, FEED_NAME, _factory)
    return feed


def _config(rules: list[dict]) -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
            "params": {"symbol": SYMBOL, "weight": 0.9},
            "universe": {"source": "explicit", "symbols": [SYMBOL]},
            "period": dict(PERIOD),
            "initial_cash": 1_000_000,
        },
        "data": {"feed": FEED_NAME},
        "risk": {"rules": rules},          # 真实链（无 allow_empty_chain）
    }


def _run(rules: list[dict]):
    from btf.runtime import BTFRuntime

    rt = BTFRuntime().load_config(_config(rules)).build()
    assert rt.risk_allow_empty is False      # 真实链，非逃生
    return rt, rt.run(persist=False)


class TestRealRiskChainEndToEnd:
    def test_max_weight_reduces_fill_vs_bare(self, _feed_registered):
        """真实链生效：max_weight=5% → 成交数量**显著小于**裸奔同策略。"""
        # 基准链取 max_weight=1.0（等价无约束，但**仍是真实链**）
        _bare_rt, bare = _run([{"name": "max_weight",
                                "params": {"max_weight": 1.0}}])
        _capped_rt, capped = _run([{"name": "max_weight",
                                    "params": {"max_weight": 0.05}}])
        bare_qty = sum(f.qty for f in bare.fills)
        capped_qty = sum(f.qty for f in capped.fills)
        assert bare_qty > 0, "基准（宽松链）须有成交"
        assert 0 < capped_qty < bare_qty, (
            f"风控未传导到成交：受限 {capped_qty} vs 宽松 {bare_qty}")

    def test_tiny_budget_rejects_and_records(self, _feed_registered):
        """预算不足一手 → 全部否决，拒单以 `risk_rejected` 进入结果。"""
        _rt, result = _run([
            {"name": "max_weight", "params": {"max_weight": 0.00001}}])
        assert result.fills == [], "预算不足一手时不应有成交"
        codes = [rejection.code for _order, rejection in result.rejections]
        assert RejectCode.RISK_REJECTED in codes, (
            f"风控否决未进结果拒单：{codes}")
        assert any("max_weight" in rejection.message
                   for _order, rejection in result.rejections), (
            "拒单 message 须含规则名（06 §10.6 可归因）")

    def test_cash_check_and_tradability_pass_in_normal_path(self,
                                                            _feed_registered):
        """三规则全链（生产配置形态）在正常路径下**放行**并成交。"""
        _rt, result = _run([
            {"name": "tradability"}, {"name": "max_weight",
                                      "params": {"max_weight": 0.5}},
            {"name": "cash_check"}])
        assert result.n_days > 0
        assert result.fills, "正常路径下三规则链不应把订单全部否掉"


# ─────────────────────────────────────────────────────────────
# 担保-1（一区 #8）风控 e2e 加深：3 → 9 例
# 覆盖：每条内置规则的**端到端拒单/放行**、规则归因、装配期纪律、
# 以及"状态面板注入"下的 ST/停牌/退市三态（真实链、无逃生开关）。
# ─────────────────────────────────────────────────────────────
def _state_feed(*, is_st: bool = False, is_suspended: bool = False,
                is_delisted: bool = False):
    """把场景 feed 包一层：`trading_states` 注入指定状态（其余照旧委托）。

    为什么用注入而非找真实数据：三态在真实主库上的**日期依赖**很强
    （ST 区间/停牌日/退市日），固定断言会脆；注入状态可让"规则真的按状态
    拒单"这条链路被**确定性**验证。规则实现本身的真实数据口径由
    `tests/unit/test_risk_rules.py` + `test_state_*` 覆盖。
    """
    from dataclasses import replace

    from tests.fixtures.scenarios import t_plus1_scenario

    inner = t_plus1_scenario().feed

    class _StateFeed:
        contract_version = CONTRACT_VERSION
        requires_explicit_symbols = getattr(inner, "requires_explicit_symbols",
                                            True)

        def __getattr__(self, name):
            return getattr(inner, name)

        def trading_states(self, date, symbols=None, *, with_touch_flags=True):
            base = inner.trading_states(date, symbols,
                                        with_touch_flags=with_touch_flags)
            out = {}
            for sym, st in dict(base).items():
                out[sym] = replace(st, is_st=is_st, is_suspended=is_suspended,
                                   is_delisted=is_delisted)
            return out

    return _StateFeed()


def _register_feed(name: str, feed) -> None:
    def _factory(**_params):
        return feed

    _factory.contract_version = CONTRACT_VERSION
    if name not in registry.available(registry.DATA_FEED):
        registry.register(registry.DATA_FEED, name, _factory)


def _run_with_feed(feed_name: str, rules: list[dict], *, symbol: str = SYMBOL,
                   initial_cash: int = 1_000_000, weight: float = 0.9):
    from btf.runtime import BTFRuntime

    cfg = _config(rules)
    cfg["data"] = {"feed": feed_name}
    cfg["run"]["universe"] = {"source": "explicit", "symbols": [symbol]}
    cfg["run"]["params"] = {"symbol": symbol, "weight": weight}
    cfg["run"]["initial_cash"] = initial_cash
    rt = BTFRuntime().load_config(cfg).build()
    return rt, rt.run(persist=False)


class TestRiskChainDeepened:
    """担保-1：真实链下的**单规则端到端**行为（拒单归因 + 状态三态）。"""

    def test_cash_check_rejects_when_insufficient(self):
        """cash_check：目标仓位 > 可用现金 → 拒单（归因到现金约束）。

        触发方式：`weight=1.2`（目标 1.2×NAV）→ 现金约束成为**唯一**绑定项
        （实测 100 万现金 / 需 1,009,242 元）；`weight=0.9` 或"现金极小"
        都不触发——后者会被再平衡器先缩量为 0 手（无订单可言，实测留痕）。
        """
        _register_feed("risk_e2e_feed", _state_feed())
        _rt, result = _run_with_feed("risk_e2e_feed", [{"name": "cash_check"}],
                                     weight=1.2)
        assert result.fills == []
        assert any("现金不足" in r.message
                   for _o, r in result.rejections), (
            [r.message for _o, r in result.rejections])

    def test_tradability_rejects_suspended(self):
        """tradability：停牌 → 拒单（状态面板注入，真实规则裁决）。"""
        _register_feed("risk_e2e_susp", _state_feed(is_suspended=True))
        _rt, result = _run_with_feed("risk_e2e_susp", [{"name": "tradability"}])
        assert result.fills == []
        assert any(r.code is RejectCode.RISK_REJECTED and "risk:" in r.message
                   for _o, r in result.rejections), (
            [r.message for _o, r in result.rejections])

    def test_tradability_rejects_delisted(self):
        """tradability：退市 → 拒单。"""
        _register_feed("risk_e2e_delist", _state_feed(is_delisted=True))
        _rt, result = _run_with_feed("risk_e2e_delist",
                                     [{"name": "tradability"}])
        assert result.fills == []
        assert any(r.code is RejectCode.RISK_REJECTED and "risk:" in r.message
                   for _o, r in result.rejections)

    def test_tradability_reject_st_flag(self):
        """tradability(`reject_st=true`)：ST → 拒单；默认 false → 放行（对照）。

        拒单 message 形如 `risk:st（风控预检）`（规则内 reason 标签而非类名）。
        """
        _register_feed("risk_e2e_st", _state_feed(is_st=True))
        _rt, result = _run_with_feed(
            "risk_e2e_st",
            [{"name": "tradability", "params": {"reject_st": True}}])
        assert result.fills == []
        assert any("risk:st" in r.message for _o, r in result.rejections), (
            [r.message for _o, r in result.rejections])
        # 负向对照：同状态、reject_st 关 → 必须放行（证明拒单来自该参数）
        _rt2, result2 = _run_with_feed("risk_e2e_st",
                                       [{"name": "tradability"}])
        assert result2.fills, "reject_st=false 时 ST 不应被拒（假阳性）"

    def test_rule_order_first_reject_wins(self):
        """多规则链：**先命中者归因**（cash_check 放行、max_weight 拒绝）。"""
        _register_feed("risk_e2e_feed", _state_feed())
        _rt, result = _run_with_feed(
            "risk_e2e_feed",
            [{"name": "cash_check"},
             {"name": "max_weight", "params": {"max_weight": 0.00001}}])
        msgs = [r.message for _o, r in result.rejections]
        assert result.fills == []
        assert any("max_weight" in m for m in msgs), msgs
        assert not any("现金不足" in m for m in msgs), (
            f"cash_check 应放行（weight=0.9 未超现金）：{msgs}")

    def test_unknown_rule_name_is_assembly_error(self):
        """装配期纪律：风控链引用未登记规则名 → 装配期即失败（不静默忽略）。"""
        from btf.runtime import BTFRuntime

        _register_feed("risk_e2e_feed", _state_feed())
        cfg = _config([{"name": "no_such_rule"}])
        with pytest.raises(Exception, match=r"no_such_rule|未知"):
            BTFRuntime().load_config(cfg).build()
