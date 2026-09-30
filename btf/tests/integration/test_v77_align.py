# -*- coding: utf-8 -*-
"""v7.7 对账集成测试（M3 任务 6.2；验收 A1/A2）。

与 `tools/align_v77.py` 同口径（脚本是 CLI 形态，本测试是 pytest 形态）：
    A1 选股一致：btf `screen_l2`（真 PIT）  vs md_core `screen_stocks_v75`
    A2 状态一致：btf `state_of`（L0-LIQ）   vs md_core `get_l0_liq_state`

纪律（不静默通过）：
    - 主库不可用 → skip；
    - md_core（赤潮）不可用/异常 → 仅跑 btf 侧自洽断言，对比项 skip；
    - ROE 口径差异（md_core 建库视角 vs btf 真 PIT）已显式留痕，
      对账以"当日"为准（同一交易日两者应一致，历史回测日可能分化）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data import parquet_reader as pr
from btf.data.fundamentals import daily_basic_of, roe_pit_of, screen_l2
from btf.data.liq import CONFIRM, CRISIS, NORMAL, RECOVERY, WATCH, LiqState, state_of

pytestmark = [pytest.mark.l4]

ROOT = Path(MARKET_DATA_DIR)
CHICHAO_ROOT = Path(r"d:\量化策略\赤潮")
MIRROR_JSON = CHICHAO_ROOT / "rules_mirror_v77.json"
TRADE_DATE = "20260923"

VALID_STATES = {NORMAL, WATCH, CRISIS, RECOVERY}


def _skip_if_no_data() -> None:
    if not (ROOT / "daily_basic").is_dir():
        pytest.skip("主库不可用")


def _prev(phase: str, days: int, status: str = CRISIS) -> LiqState:
    """合成前一日状态（Z-6 单步递推测试用；抽掉真实数据依赖）。"""
    return LiqState(date="20260922", status=status, sh_pct=0.0, small_pct=0.0,
                    down_cnt=0, down_ratio=0.0, triggers=(),
                    raw=status, phase=phase, phase_days=days)


def _load_rules() -> tuple[dict, dict]:
    """读取赤潮规则镜像（真源）→ (L2_filter, L0_LIQ)；缺失则 skip。"""
    if not MIRROR_JSON.is_file():
        pytest.skip(f"规则镜像缺失：{MIRROR_JSON}")
    payload = json.loads(MIRROR_JSON.read_text(encoding="utf-8"))
    rules = payload.get("rules") or {}
    l2, liq = rules.get("L2_filter"), rules.get("L0_LIQ")
    if not l2 or not liq:
        pytest.skip("镜像 JSON 缺 L2_filter / L0_LIQ")
    return l2, liq


def _mdcore():
    """导入外部工程 md_core（只读）；不可用 → skip。"""
    if str(CHICHAO_ROOT) not in sys.path:
        sys.path.insert(0, str(CHICHAO_ROOT))
    try:
        import md_core as md
    except Exception as exc:                       # 环境差异（依赖/联网）
        pytest.skip(f"md_core 不可用：{exc}")
    return md


class TestA1Selection:
    def test_screen_l2_deterministic_and_sorted(self):
        """确定性：同输入同输出且升序（引擎/对账纪律）。"""
        _skip_if_no_data()
        l2, _ = _load_rules()
        picks = screen_l2(TRADE_DATE, l2, root=ROOT)
        assert picks == sorted(picks)
        assert picks == screen_l2(TRADE_DATE, l2, root=ROOT)

    def test_screen_l2_criteria_self_consistent(self):
        """复算纪律：每只入选标的当日 pe/pb/roe/dv 均满足镜像阈值。"""
        _skip_if_no_data()
        l2, _ = _load_rules()
        picks = screen_l2(TRADE_DATE, l2, root=ROOT)
        assert picks, "该交易日应选出标的（镜像阈值宽松）"
        basics = daily_basic_of(TRADE_DATE, ROOT)
        roes = roe_pit_of(TRADE_DATE, ROOT)
        for code in picks:
            row, roe = basics[code], roes[code][0]
            assert 0 < row["pe_ttm"] < l2["pe_max"]
            assert row["pb"] < l2["pb_max"]
            assert roe > l2["roe_min"] and row["dv_ttm"] > l2["dv_min"]

    def test_roe_pit_no_lookahead(self):
        """PIT 纪律：采信年报的 ann_date ≤ 对账日（不采信未公告年报）。"""
        _skip_if_no_data()
        _load_rules()
        roes = roe_pit_of(TRADE_DATE, ROOT)
        assert roes
        table = pr.read_range(
            "fina_indicator", ROOT, f"{int(TRADE_DATE[:4]) - 3}0101", TRADE_DATE,
            columns=["ts_code", "ann_date", "end_date", "roe_waa"])
        rows = table.to_pylist()
        for code in sorted(roes)[:20]:
            visible = {
                str(r["end_date"]) for r in rows
                if r.get("ts_code") == code and r.get("roe_waa") is not None
                and str(r.get("ann_date") or "") <= TRADE_DATE
                and str(r.get("end_date") or "").endswith("1231")
            }
            assert roes[code][1] in visible, (
                f"{code} 采信了未公告年报 {roes[code][1]}（前视）")

    def test_a1_matches_md_core(self):
        """A1：同日选股名单与 md_core `screen_stocks_v75` 完全一致。"""
        _skip_if_no_data()
        _mdcore()                     # 确保 md_core 可导入（不可用则 skip）
        l2, _ = _load_rules()
        btf_picks = screen_l2(TRADE_DATE, l2, root=ROOT)
        try:
            from md_core.screening import screen_stocks_v75
            table = screen_stocks_v75(
                trade_date=TRADE_DATE, pe_max=l2["pe_max"], pb_max=l2["pb_max"],
                roe_min=l2["roe_min"], dv_min=l2["dv_min"])
            md_picks = sorted(table["ts_code"].tolist())
        except Exception as exc:
            pytest.skip(f"md_core 选股失败：{exc}")
        only_btf = sorted(set(btf_picks) - set(md_picks))
        only_md = sorted(set(md_picks) - set(btf_picks))
        assert not only_btf and not only_md, (
            f"A1 名单差异：仅 btf {len(only_btf)} 只{only_btf[:5]}；"
            f"仅 md_core {len(only_md)} 只{only_md[:5]}")


class TestA2Liq:
    def test_state_valid_on_real_data(self):
        """真实数据下状态取值合法且指标就位。"""
        _skip_if_no_data()
        _, liq = _load_rules()
        st = state_of(TRADE_DATE, liq, root=ROOT)
        assert st.status in VALID_STATES
        assert st.date == TRADE_DATE
        assert st.down_cnt >= 0 and st.down_ratio >= 0.0

    def test_confirm_and_recovery_sequence(self):
        """确认期 + RECOVERY 序列（**Z-6** 口径；真实数据日 × 合成前态）。

        推进三段：① 危机日之后 → 最终态恒为 CRISIS（确认期起点，0 成）；
        ② 确认期已过 2 日 + 今日平静 → 第 3 个平静日**入闸** RECOVERY；
        ③ 恢复期已过 2 日 + 今日平静 → NORMAL。
        """
        _skip_if_no_data()
        _, liq = _load_rules()
        base = state_of(TRADE_DATE, liq, root=ROOT)
        # ① 前一日为危机 → 今日：原始判定 WATCH 也按 CRISIS（确认期禁止新增风险）
        after = state_of(TRADE_DATE, liq, root=ROOT,
                         prev=_prev(CONFIRM, 0))
        assert after.status == CRISIS, (base.status, after.status)
        assert after.raw == base.status, (after.raw, base.status)
        if base.status == NORMAL:
            assert (after.phase, after.phase_days) == (CONFIRM, 1), after
        else:
            # WATCH/CRISIS 日：打断/归零 → 计数 0（仍为 CRISIS）
            assert (after.phase, after.phase_days) == (CONFIRM, 0), after
        # ② 确认期第 3 个平静日 → 入闸；③ 恢复期满 → NORMAL
        third = state_of(TRADE_DATE, liq, root=ROOT, prev=_prev(CONFIRM, 2))
        sixth = state_of(TRADE_DATE, liq, root=ROOT, prev=_prev(RECOVERY, 2))
        if base.status == NORMAL:
            assert third.status == RECOVERY, third
            assert sixth.status == NORMAL, sixth

    def test_a2_matches_md_core(self):
        """A2：同日状态与 md_core `get_l0_liq_state` 一致。"""
        _skip_if_no_data()
        _mdcore()                     # 确保 md_core 可导入（不可用则 skip）
        _, liq = _load_rules()
        btf_state = state_of(TRADE_DATE, liq, root=ROOT)
        try:
            from md_core.market_state import get_l0_liq_state
            md_status = get_l0_liq_state(trade_date=TRADE_DATE).get("status")
        except Exception as exc:
            pytest.skip(f"md_core 状态失败：{exc}")
        assert md_status == btf_state.status, (
            f"A2 状态不一致：btf={btf_state.status} md_core={md_status}")


class TestA3RuleSourceConsistency:
    """A3（09 §14.7）：override 为空 → 参数逐项等于镜像 JSON；指纹可复核。"""

    def test_overrides_empty_params_equal_mirror(self):
        from btf.strategy.rules import MirrorJsonRulesProvider

        payload = json.loads(MIRROR_JSON.read_text(encoding="utf-8"))
        provider = MirrorJsonRulesProvider(MIRROR_JSON)
        assert provider.overrides == ()
        for rule_id, params in payload["rules"].items():
            assert dict(provider.get(rule_id)) == params

    def test_fingerprint_stable_and_prefixed(self):
        from btf.strategy.rules import MirrorJsonRulesProvider

        provider = MirrorJsonRulesProvider(MIRROR_JSON)
        assert provider.rules_version() == provider.rules_version()
        assert provider.rules_version().startswith("v7.7@sha256:")


class TestA4UpgradeRegression:
    """A4（09 §14.7）：v7.7→v7.8 单参数变更（pe_max 30→25）→ diff 受控。"""

    def test_single_param_upgrade_diff_confined(self):
        _skip_if_no_data()
        payload = json.loads(MIRROR_JSON.read_text(encoding="utf-8"))
        base = payload["rules"]["L2_filter"]
        tightened = {**base, "pe_max": 25.0}

        before = screen_l2(TRADE_DATE, base, root=ROOT)
        after = screen_l2(TRADE_DATE, tightened, root=ROOT)
        assert set(after) <= set(before), "收紧阈值必须给出原名单的子集"

        basics = daily_basic_of(TRADE_DATE, ROOT)
        dropped = sorted(set(before) - set(after))
        for code in dropped:                       # 仅阈值边界内的标的受影响
            pe = basics[code]["pe_ttm"]
            assert 25.0 <= pe < base["pe_max"], f"{code} pe={pe} 不应受影响"
        survivors = {c for c in before if basics[c]["pe_ttm"] < 25.0}
        assert set(after) == survivors, "边界外标的零变化"
        assert dropped, "该交易日应存在边界内标的（否则 diff 断言空转）"
