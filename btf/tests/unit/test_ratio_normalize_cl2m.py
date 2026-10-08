# -*- coding: utf-8 -*-
"""**R54′（N58＋N59 合并；19 号 §102.8）**：比率归一化 helper 全链路用例。

三态语义（`btf.domain.action.ratio_or_default`）：
    ① `None` ⇒ `default`（静默）；② **不可转 float** ⇒ `default` ＋ **WARN**（禁崩）；
    ③ **≤0** ⇒ **无效值 ⇒ 不建模（份额不变）**⇒ `default` ＋ **WARN**（禁清零——
       老大 2026-10-08 裁决 (a)；§102.1）。

覆盖 6 形输入（`None`/`""`/`"abc"`/`0.0`/`-1`/`0.32665`）与 5 条读/用路径：
helper 本体 ／ `corp_action_probe` 装载（**含开关打开**的 `consolidations` 路径——
默认 off 会掩盖）／ `events_log._dec_action` 回读 ／ `portfolio` 消费端（禁清零）／
`action_order_key`（ETF 分支，禁崩）。
"""
from __future__ import annotations

import logging

import pytest
from btf.domain.action import CorporateAction, ratio_or_default
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l1]

D0 = TradingDate.from_ymd("20240102")
D1 = TradingDate.from_ymd("20241231")
NORMAL = 0.32665                       # 真实清单唯一值（000887.SZ）
DIRTY = [None, "", "abc", 0.0, -1]


def _expected(value) -> float:
    return NORMAL if value == NORMAL else 1.0


class TestHelperThreeStates:
    @pytest.mark.parametrize("value,expected", [
        (None, 1.0), ("", 1.0), ("abc", 1.0), (0.0, 1.0), (-1, 1.0),
        (-0.5, 1.0), (NORMAL, NORMAL), ("0.5", 0.5), (2.0, 2.0),
        (float("nan"), 1.0), (float("inf"), 1.0), (False, 1.0),
    ])
    def test_values(self, value, expected):
        assert ratio_or_default(value) == pytest.approx(expected)

    def test_warn_injection_unconvertible_and_nonpositive(self):
        """告警**策略在单点**（domain 不持全局副作用）：`warn` 回调注入。"""
        seen: list[str] = []
        assert ratio_or_default("", context="t1", warn=seen.append) == 1.0
        assert ratio_or_default(0.0, context="t2", warn=seen.append) == 1.0
        assert ratio_or_default(float("nan"), warn=seen.append) == 1.0
        text = "\n".join(seen)
        assert "不可转换为数值" in text          # N58：禁崩
        assert "非有限值" in text
        assert "不建模" in text                  # N59 裁决 (a)：禁清零

    def test_none_is_silent(self):
        seen: list[str] = []
        assert ratio_or_default(None, warn=seen.append) == 1.0
        assert seen == []

    def test_normal_value_silent(self):
        seen: list[str] = []
        assert ratio_or_default(NORMAL, warn=seen.append) == pytest.approx(NORMAL)
        assert seen == []


class TestProbeLoadPaths:
    """`corp_action_probe.to_corporate_actions` 两条装载路径（含开关打开）。"""

    @staticmethod
    def _data(ratio):
        return {
            "consolidations_confirmed": [
                {"symbol": "000887.SZ", "ex_date": "20240103",
                 "consolidation_ratio": ratio}],
            "consolidations": [
                {"symbol": "000887.SZ", "ex_date": "20240103",
                 "consolidation_ratio": ratio}],
        }

    @pytest.mark.parametrize("ratio", [*DIRTY, NORMAL])
    def test_confirmed_never_crashes(self, ratio):
        from btf.data.corp_action_probe import to_corporate_actions

        acts = to_corporate_actions(self._data(ratio), D0, D1)
        got = {a.consolidation_source: a.consolidation_ratio for a in acts}
        assert got["confirmed"] == pytest.approx(_expected(ratio))

    @pytest.mark.parametrize("ratio", DIRTY)
    def test_proxy_switch_on_path_never_crashes(self, ratio, monkeypatch):
        from btf.data.corp_action_probe import to_corporate_actions

        monkeypatch.setenv("BTFTOOL_CORP_ACTIONS_CONSOLIDATIONS", "on")
        acts = to_corporate_actions(self._data(ratio), D0, D1)
        proxy = [a for a in acts if a.consolidation_source == "proxy"]
        assert len(proxy) == 1                    # 开关打开 ⇒ 代理路径已走到
        assert proxy[0].consolidation_ratio == pytest.approx(1.0)

    @pytest.mark.parametrize("ratio", DIRTY)
    def test_proxy_switch_off_skips(self, ratio, monkeypatch):
        from btf.data.corp_action_probe import to_corporate_actions

        monkeypatch.delenv("BTFTOOL_CORP_ACTIONS_CONSOLIDATIONS",
                           raising=False)
        acts = to_corporate_actions(self._data(ratio), D0, D1)
        assert not [a for a in acts if a.consolidation_source == "proxy"]


class TestEventLogReadback:
    """`events_log._dec_action` 回读（外部/旧 JSONL 脏值）。"""

    @pytest.mark.parametrize("ratio", [*DIRTY, NORMAL])
    def test_readback_never_crashes(self, ratio, caplog):
        from btf.engine.events_log import _dec_action

        with caplog.at_level(logging.WARNING, logger="btf.engine.events_log"):
            act = _dec_action({
                "symbol": "000887.SZ", "ex_date": "20240103",
                "pay_date": None, "record_date": None, "ann_date": None,
                "cash_div_per_share": 0.0, "stk_div_per_share": 0.0,
                "consolidation_ratio": ratio,
            })
        assert act.consolidation_ratio == pytest.approx(_expected(ratio))
        # `None` 属「无值」⇒ 静默（不告警）；其余脏值必须 WARN（禁静默）
        if ratio in DIRTY and ratio is not None:
            assert caplog.records, f"脏值 {ratio!r} 应产生 WARN"


class TestPortfolioConsumer:
    """消费端：**≤0 ⇒ 不建模（份额不变）**，禁走保底清零（N59 裁决 (a)）。"""

    @staticmethod
    def _act(ratio):
        return CorporateAction(
            symbol="000887.SZ", ex_date=D0, pay_date=None, record_date=None,
            cash_div_per_share=0.0, stk_div_per_share=0.0, ann_date=None,
            consolidation_ratio=ratio, consolidation_source="confirmed")

    @pytest.mark.parametrize("ratio", DIRTY)
    def test_invalid_ratio_no_consolidation_no_wipeout(self, ratio):
        from btf.portfolio.portfolio import Portfolio, Position

        p = Portfolio(cash=0.0)
        p.positions["000887.SZ"] = Position(symbol="000887.SZ", qty=100,
                                            available_qty=100, avg_cost=10.0)
        p._last_close["000887.SZ"] = 10.0
        p.apply_ex_date(self._act(ratio))
        assert p.consolidations == []             # 不建模
        pos = p.positions["000887.SZ"]
        assert pos.qty == 100                     # 份额不变（非 1 股清零）
        assert pos.avg_cost == pytest.approx(10.0)

    def test_normal_ratio_still_modeled(self):
        from btf.portfolio.portfolio import Portfolio, Position

        p = Portfolio(cash=0.0)
        p.positions["000887.SZ"] = Position(symbol="000887.SZ", qty=100,
                                            available_qty=100, avg_cost=10.0)
        p._last_close["000887.SZ"] = 10.0
        p.apply_ex_date(self._act(NORMAL))
        ev = p.consolidations[0]
        assert ev["qty_after"] == 32 and "forced_min_qty" not in ev

    def test_floor_branch_unchanged_for_tiny_positive(self):
        """R21′ 保底分支**不受影响**（合法极小比例仍走保底＋WARN 标记）。"""
        from btf.portfolio.portfolio import Portfolio, Position

        p = Portfolio(cash=0.0)
        p.positions["000887.SZ"] = Position(symbol="000887.SZ", qty=100,
                                            available_qty=100, avg_cost=10.0)
        p._last_close["000887.SZ"] = 10.0
        p.apply_ex_date(self._act(0.005))
        assert p.consolidations[0]["forced_min_qty"] is True


class TestOrderKeyDirty:
    """`action_order_key`（ETF 分支）经归一化：脏值不崩、≤0 归非折算类。"""

    @staticmethod
    def _act(ratio):
        return CorporateAction(
            symbol="510310.SH", ex_date=D0, pay_date=None, record_date=None,
            cash_div_per_share=0.0, stk_div_per_share=0.0, ann_date=None,
            consolidation_ratio=ratio, consolidation_source="fund_conv")

    @pytest.mark.parametrize("ratio", DIRTY)
    def test_dirty_ratio_key_is_one(self, ratio):
        from btf.data.etf_corp_actions import action_order_key

        assert action_order_key(self._act(ratio))[2] == 1

    def test_normal_ratio_key_is_zero(self):
        from btf.data.etf_corp_actions import action_order_key

        assert action_order_key(self._act(0.5))[2] == 0
