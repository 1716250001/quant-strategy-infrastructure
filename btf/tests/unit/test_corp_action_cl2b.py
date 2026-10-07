# -*- coding: utf-8 -*-
"""L1 单测：**CL-2 第二段**（19 号 §79）——扩维 / 原子入账 / 缩股 / 编解码 / 清单。

判据（§79.5）：
  A 单股端到端手算逐位一致（portfolio 层，N=1000 ⇒ 获配 150、缴款 2,164.5）；
  B″ 零回归：旧事件（无配股/缩股字段）路径**逐位不变**；
  D 铁律：不动 `adj_factor` 消费逻辑、不把复权价引入引擎（本文件不断言复权）；
  E 清单方向：向上＝除权类 / 向下＝缩股候选（`corp_action_probe` 断言）。
"""
from __future__ import annotations

import pytest
from btf.domain.action import CorporateAction
from btf.domain.types import TradingDate
from btf.portfolio.portfolio import Portfolio, Position

pytestmark = [pytest.mark.l1]

D1 = TradingDate.from_iso(2022, 1, 18)
D2 = TradingDate.from_iso(2022, 1, 27)      # 除权日（本地 adj_factor 跳变真值）
D3 = TradingDate.from_iso(2022, 2, 15)      # 获配上市日


def _rights_action(**kw) -> CorporateAction:
    base = dict(symbol="600030.SH", ex_date=D2, pay_date=None, record_date=D1,
                cash_div_per_share=0.0, stk_div_per_share=0.0, ann_date=None,
                rights_ratio=0.15, rights_price=14.43, rights_ex_date=D2,
                rights_listing_date=D3)
    base.update(kw)
    return CorporateAction(**base)


def _legacy_action() -> CorporateAction:
    return CorporateAction(symbol="000001.SZ", ex_date=D2, pay_date=D3,
                           record_date=D1, cash_div_per_share=0.5,
                           stk_div_per_share=0.2, ann_date=None)


class TestCorporateActionExt:
    def test_defaults_backward_compatible(self):
        """R3′：新字段默认 = 无配股/无缩股 ⇒ 旧构造点语义不变。"""
        a = _legacy_action()
        assert a.rights_ratio == 0.0 and a.rights_price == 0.0
        assert a.rights_ex_date is None and a.rights_listing_date is None
        assert a.consolidation_ratio == 1.0

    def test_events_log_roundtrip_new_fields(self):
        """R5′：事件日志编解码含新字段且往返相等（含旧日志兼容）。"""
        from btf.engine.events_log import _dec_action, _enc_action

        a = _rights_action()
        assert _dec_action(_enc_action(a)) == a
        cons = CorporateAction(symbol="000887.SZ", ex_date=D2, pay_date=None,
                               record_date=None, cash_div_per_share=0.0,
                               stk_div_per_share=0.0, ann_date=None,
                               consolidation_ratio=0.5)
        assert _dec_action(_enc_action(cons)) == cons
        # 旧日志（无新键）⇒ 解码为默认值（行为与改动前一致）
        legacy_payload = {
            "symbol": "000001.SZ", "ex_date": D2.to_ymd(), "pay_date": None,
            "record_date": None, "cash_div_per_share": 0.5,
            "stk_div_per_share": 0.2, "ann_date": None,
        }
        back = _dec_action(legacy_payload)
        assert back.rights_ratio == 0.0 and back.consolidation_ratio == 1.0


class TestRightsBookingCl2b:
    def _portfolio(self) -> Portfolio:
        p = Portfolio(cash=1_000_000.0)
        p.positions["600030.SH"] = Position(
            symbol="600030.SH", qty=1000, available_qty=1000, avg_cost=25.0)
        p._last_close["600030.SH"] = 25.70
        return p

    def test_judgement_a_hand_calc_bitwise(self):
        """判据 A（手算）：M=floor(1000×0.15)=150；缴款 150×14.43=2164.5；
        avg_cost=(1000×25.0+2164.5)/1150；除权日 NAV **≈ 不变**（理论除权价重估）。"""
        p = self._portfolio()
        nav_before = p.total_value()
        qty_at_ex = p.apply_ex_date(_rights_action())

        assert qty_at_ex == 1000
        pos = p.positions["600030.SH"]
        assert pos.qty == 1150 and pos.available_qty == 1150
        assert p.cash == pytest.approx(1_000_000.0 - 2164.5, rel=1e-12)
        assert pos.avg_cost == pytest.approx(
            (1000 * 25.0 + 2164.5) / 1150, rel=1e-12)
        assert p._last_close["600030.SH"] == pytest.approx(
            (25.70 + 0.15 * 14.43) / 1.15, rel=1e-12)
        assert p.total_value() == pytest.approx(nav_before, rel=1e-12)
        ev = p.rights_events[0]
        assert ev["rights_qty"] == 150 and ev["cost"] == pytest.approx(2164.5)
        assert ev["cash_shortfall"] == 0.0
        assert ev["listing_date"] == D3.to_ymd()

    def test_cash_shortfall_recorded_not_silent(self):
        """§79.3 #2：现金不足 ⇒ 默认外部增资补足，但**必须可观测**。"""
        p = Portfolio(cash=100.0)
        p.positions["600030.SH"] = Position(symbol="600030.SH", qty=1000,
                                            available_qty=1000, avg_cost=25.0)
        p._last_close["600030.SH"] = 25.70
        p.apply_ex_date(_rights_action())
        ev = p.rights_events[0]
        assert ev["cash_shortfall"] == pytest.approx(2164.5 - 100.0)
        assert p.cash == pytest.approx(100.0 - 2164.5)

    def test_no_rights_bitwise_zero_regression(self):
        """判据 B″：无配股字段 ⇒ 除权路径与改动前**逐位一致**。"""
        p = self._portfolio()
        before = (dict(p.positions["600030.SH"].__dict__), p.cash,
                  dict(p._last_close))
        p.apply_ex_date(_rights_action(rights_ratio=0.0, rights_price=0.0))
        after = (dict(p.positions["600030.SH"].__dict__), p.cash,
                 dict(p._last_close))
        assert before == after
        assert p.rights_events == []


class TestConsolidationCl2b:
    def test_consolidation_qty_down_cost_up_no_cash(self):
        """R6′：qty 按比例减少（无现金流）；成本总额守恒、每股价放大。"""
        p = Portfolio(cash=1_000.0)
        p.positions["000887.SZ"] = Position(symbol="000887.SZ", qty=1000,
                                            available_qty=1000, avg_cost=2.0)
        p._last_close["000887.SZ"] = 3.0
        a = CorporateAction(symbol="000887.SZ", ex_date=D2, pay_date=None,
                            record_date=None, cash_div_per_share=0.0,
                            stk_div_per_share=0.0, ann_date=None,
                            consolidation_ratio=0.5)
        p.apply_ex_date(a)
        pos = p.positions["000887.SZ"]
        assert p.cash == 1_000.0                     # 无现金流
        assert pos.qty == 500 and pos.available_qty == 500
        assert pos.avg_cost == pytest.approx(4.0)
        assert p._last_close["000887.SZ"] == pytest.approx(6.0)
        assert p.consolidations[0]["qty_after"] == 500


class TestProbeArtifactCl2b:
    def test_to_corporate_actions_window_and_fields(self, monkeypatch):
        """清单 → 事件：区间过滤 + 四要素映射正确（含缩股；显式开以测映射）。"""
        from btf.data.corp_action_probe import to_corporate_actions

        monkeypatch.setenv("BTFTOOL_CORP_ACTIONS_CONSOLIDATIONS", "on")
        data = {
            "rights": [{"symbol": "600030.SH", "ex_date": "20220127",
                        "ratio": 0.15, "price": 14.43,
                        "record_date": "20220118", "listing_date": "20220215"}],
            "consolidations": [{"symbol": "000887.SZ", "ex_date": "20070330",
                                "consolidation_ratio": 0.5}],
        }
        acts = to_corporate_actions(data, TradingDate.from_ymd("20220101"),
                                    TradingDate.from_ymd("20220201"))
        assert len(acts) == 1
        a = acts[0]
        assert a.rights_ratio == 0.15 and a.rights_price == 14.43
        assert a.ex_date == D2 and a.rights_listing_date == D3
        assert a.cash_div_per_share == 0.0 and a.stk_div_per_share == 0.0
        both = to_corporate_actions(data, TradingDate.from_ymd("19910101"),
                                    TradingDate.from_ymd("20261231"))
        assert len(both) == 2
        cons = next(x for x in both if x.symbol == "000887.SZ")
        assert cons.consolidation_ratio == 0.5

    def test_consolidations_switch(self, monkeypatch):
        """R9′（§80.6 裁决）：默认**只披露不建模**（强制 off）；
        `BTFTOOL_CORP_ACTIONS_CONSOLIDATIONS=on` ⇒ 显式开启（对照实验用）。"""
        from btf.data.corp_action_probe import (
            consolidations_modeled,
            to_corporate_actions,
        )

        data = {
            "rights": [{"symbol": "600030.SH", "ex_date": "20220127",
                        "ratio": 0.15, "price": 14.43,
                        "record_date": "20220118", "listing_date": "20220215"}],
            "consolidations": [{"symbol": "000887.SZ", "ex_date": "20070330",
                                "consolidation_ratio": 0.5}],
        }
        monkeypatch.delenv("BTFTOOL_CORP_ACTIONS_CONSOLIDATIONS", raising=False)
        assert consolidations_modeled() is False
        assert len(to_corporate_actions(data, TradingDate.from_ymd("19910101"),
                                        TradingDate.from_ymd("20261231"))) == 1
        monkeypatch.setenv("BTFTOOL_CORP_ACTIONS_CONSOLIDATIONS", "on")
        assert consolidations_modeled() is True
        acts = to_corporate_actions(data, TradingDate.from_ymd("19910101"),
                                    TradingDate.from_ymd("20261231"))
        assert len(acts) == 2
        assert any(x.consolidation_ratio == 0.5 for x in acts)


class TestForwardValidationR8c:
    """R8′（19 号 §80.3 / §81.2 G1+G2）：正推判定 ＋ 放大因子守卫。"""

    def test_forward_primary_and_reverse_banned_when_amp_gt5(self):
        """正推通过 ⇒ validated；即便 amp>5（反推数值病态）也**不得产出反推结论**。"""
        from btf.data.corp_action_probe import AMP_GUARD, VALIDATION_TOL, _validate_right_math

        # 600030.SH 形态：x_act=25.70、r=0.15、Pr=14.43 ⇒ chg_pred≈1.06068
        m = _validate_right_math(0.15, 14.43, 1.06068, 25.70)
        assert m["forward_err"] is not None
        assert m["forward_err"] <= VALIDATION_TOL          # 正推通过
        assert m["amp_factor"] > AMP_GUARD                 # 13.7x ⇒ 反推禁用
        assert m["x_pred"] is None and m["rel_err"] is None

    def test_amp_guard_at_39_5_no_reverse_conclusion(self):
        """G2：amp≈39.5 的病态格 ⇒ 反推字段必须为空、判定只来自正推（挂起）。"""
        from btf.data.corp_action_probe import _validate_right_math

        # 解 chg 使 amp = chg(1+r)/|1+r−chg| ≈ 39.5（r=0.3, Pr=3.8, x_act=5.77）
        m = _validate_right_math(0.3, 3.8, 1.25858, 5.77)
        assert m["amp_factor"] == pytest.approx(39.5, abs=0.1)
        assert m["x_pred"] is None and m["rel_err"] is None   # 反推禁用
        assert m["forward_err"] > 0.005                       # 正推不通过 ⇒ 挂起

    def test_forward_rescues_reverse_ill_conditioned_case(self):
        """§80.3 铁证（600079.SH 20060807）：旧反推误杀（rel 0.52%）；
        新正推 0.073% ⇒ validated（79 条同族被救回的数学根据）。"""
        from btf.data.corp_action_probe import VALIDATION_TOL, _validate_right_math

        m = _validate_right_math(0.3, 3.8, 1.084594, 5.77)
        assert m["forward_err"] == pytest.approx(0.000861, abs=1e-4)
        assert m["forward_err"] <= VALIDATION_TOL
        assert m["amp_factor"] == pytest.approx(6.55, abs=0.05)  # >5 ⇒ 反推禁用
        assert m["x_pred"] is None

    def test_repo_artifact_present_and_direction_ok(self):
        """判据 E ＋ R8′/R9′（G1/G3/G5）：清单存在、正推字段齐、方向不反；
        rights＝**165**（G1）、known-16 全部在 rights、挂起 11 条带两列、
        缩股门槛后 **0 存活**（全部入 uncorroborated 带 reason）。"""
        from btf.data.corp_action_probe import PROBE_SCHEMA, load_actions

        data = load_actions()
        assert data is not None, "仓库内缺 corp_actions.json（feed adapter 将报未加载）"
        assert data["schema"] == PROBE_SCHEMA
        # G1：86 → 165（正推判据救回 79 条被病态反推误杀的真配股）
        assert data["stats"]["rights_validated"] == 165
        assert data["stats"]["rights_suspended"] == 11
        for x in data["rights"]:
            assert {"symbol", "ex_date", "ratio", "price",
                    "chg_pred", "forward_err", "amp_factor"} <= set(x)
            assert x["ratio"] > 0 and x["chg"] > 1.0      # 向上＝除权类
            assert 0.0 <= x["forward_err"] <= 0.005       # 正推容差
        for x in data["rights_suspended"]:                # 挂起带两列（G1）
            assert {"amp_factor", "forward_err"} <= set(x)
            assert x["forward_err"] > 0.005
        known = {("600030.SH", "20220127"), ("002948.SZ", "20220113"),
                 ("000970.SZ", "20220224"), ("601108.SH", "20220412"),
                 ("000065.SZ", "20220414"), ("600958.SH", "20220429"),
                 ("601377.SH", "20220825"), ("300772.SZ", "20221118"),
                 ("601016.SH", "20221128"), ("600459.SH", "20221222"),
                 ("600888.SH", "20210421"), ("600909.SH", "20210610"),
                 ("601015.SH", "20210708"), ("601236.SH", "20210804"),
                 ("002142.SZ", "20211202"), ("601555.SH", "20211223")}
        in_rights = {(x["symbol"], x["ex_date"]) for x in data["rights"]}
        missing = known - in_rights
        assert not missing, f"known-16 未全部进入 rights：{missing}"
        # G5：000816 / 002232 已入建模集；000516 正推误差 0.98% ⇒ 按纪律仍挂起
        #（与 N16「11 条真口径差」自洽；「三条全入建模」与 G1 容差冲突处待裁——§九·Z）
        assert ("000816.SZ", "20110209") in in_rights
        assert ("002232.SZ", "20100322") in in_rights
        susp516 = [x for x in data["rights_suspended"]
                   if x["symbol"] == "000516.SZ" and x["ex_date"] == "20091020"]
        assert susp516 and susp516[0]["forward_err"] == pytest.approx(
            0.009831, abs=1e-5)
        # G3（R9′）：门槛后缩股 **0 存活**；未过者全量保留披露（带 reason）
        assert data["stats"]["consolidation_corroborated"] == 0
        assert len(data["consolidations"]) == 0
        for x in data["consolidations_uncorroborated"]:
            assert x["consolidation_ratio"] > 0.95 or x.get("reason")
            assert "reason" in x
        # G4 补录（R13′/N19）：挂起条目带股本级披露（**仅披露不建模**——§82.4②）；
        # 000516 三值与审查方逐位（0.26720/0.8907/0.0507%）；10 条 r_eff 误差 ≤0.0739%、
        # 300176 反而变大（真噪声保持挂起）。
        for x in data["rights_suspended"]:
            assert {"r_effective", "subscription_ratio",
                    "forward_err_eff"} <= set(x)
        v516 = next(x for x in data["rights_suspended"]
                    if x["symbol"] == "000516.SZ")
        assert v516["r_effective"] == pytest.approx(0.267196, abs=1e-5)
        assert v516["subscription_ratio"] == pytest.approx(0.890652, abs=1e-5)
        assert v516["forward_err_eff"] == pytest.approx(0.000507, abs=1e-5)
        r10 = {"000507.SZ", "000516.SZ", "000589.SZ", "000758.SZ",
               "002017.SZ", "002130.SZ", "002500.SZ", "002634.SZ",
               "002682.SZ", "300374.SZ"}
        for x in data["rights_suspended"]:
            if x["symbol"] in r10:
                assert x["forward_err_eff"] <= 0.000739
        v300 = next(x for x in data["rights_suspended"]
                    if x["symbol"] == "300176.SZ")
        assert v300["forward_err_eff"] > v300["forward_err"]
