# -*- coding: utf-8 -*-
"""规则四方机检回归（M3 任务 6.1；H2 单一真源）。

用**合成四方 fixture**（规则源 MD / md_core paths.py / screening.py /
btf 镜像 JSON）验证机检本身有效：
    一致 → PASS(0)；任一方篡改 → FAIL(2)；文件缺失 → ERROR(3)。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import check_rules_four_way as checker
import export_rules_mirror as exporter

pytestmark = [pytest.mark.l2]

V75 = {"pe_max": 30.0, "pb_max": 3.0, "roe_min": 5.0, "dv_min": 1.0}
LIQ = {"sh_crisis": -5.0, "sh_watch": -3.0, "small_crisis": -6.0,
       "small_watch": -4.0, "down_crisis": 800, "down_watch": 300,
       "ratio_crisis": 10.0, "ratio_watch": 5.0}

SOURCE_MD = """# 赤潮融合架构 v7.7 — 单一规则源

| 规则 | 内容 |
|---|---|
| L0-LIQ | CRISIS=上证≤-5% AND 跌停占比≥10% OR 跌停≥800 OR 小盘≤-6%；WATCH=上证≤-3% 或跌停≥300 或跌停占比≥5% 或小盘≤-4% |
| L0-LIQ 恢复 | 救市脉冲不自动恢复、3日恢复机制 |

RECOVERY: CRISIS后连续3日无危机+无WATCH → RECOVERY → 3日 → NORMAL
入闸确认期归属 = CRISIS（Z-6 澄清：确认期内仓位 0、禁止新增风险）

六层体系：
├─ 基本面过滤: PE<30+PB<3+ROE>5%+股息率>1%
"""

MDCORE_STATE_PY = '''
def market_state(trade_date, ...):
    """...
    其余 NORMAL。RECOVERY（救市脉冲不自动恢复/3日恢复机制）需连续状态序列，
    本函数不判，由调用方结合连续 3 日 status 判断。
    """
'''

PATHS_PY = f'''
V75_DEFAULTS = {{"pe_max": {V75["pe_max"]}, "pb_max": {V75["pb_max"]},
                "roe_min": {V75["roe_min"]}, "dv_min": {V75["dv_min"]}}}

LIQ_THRESHOLDS = {{"sh_crisis": {LIQ["sh_crisis"]}, "sh_watch": {LIQ["sh_watch"]},
                  "small_crisis": {LIQ["small_crisis"]},
                  "small_watch": {LIQ["small_watch"]},
                  "down_crisis": {LIQ["down_crisis"]},
                  "down_watch": {LIQ["down_watch"]},
                  "ratio_crisis": {LIQ["ratio_crisis"]},
                  "ratio_watch": {LIQ["ratio_watch"]}}}
'''

#: ③ btf 侧实现 fixture（X-4 / Z-6 状态机语义锚点；合成，不读真实 liq.py）
LIQ_PY_GOOD = '''
def _finalize(date_ymd, thresholds, sh_pct, small_pct, down_cnt, down_ratio,
              prev, recovery_days: int = 3, confirm_days: int = 3):
    phase = prev.phase if prev is not None else IDLE
    days = prev.phase_days if prev is not None else 0
    if raw == CRISIS:
        status, phase, days = CRISIS, CONFIRM, 0
    elif raw == WATCH:
        if phase == IDLE:
            status, phase, days = WATCH, IDLE, 0
        else:
            status, phase, days = CRISIS, CONFIRM, 0
    elif phase == CONFIRM:
        if days + 1 < confirm_days:
            status, days = CRISIS, days + 1
        else:
            status, phase, days = RECOVERY, RECOVERY, 0
    elif phase == RECOVERY:
        if days + 1 < recovery_days:
            status, days = RECOVERY, days + 1
        else:
            status, phase, days = NORMAL, IDLE, 0
    else:
        status, phase, days = NORMAL, IDLE, 0
'''

#: X-1 回归样本：含自我维持子句（状态一旦进入 RECOVERY 永不退出）
LIQ_PY_SELF_HOLD = LIQ_PY_GOOD.replace(
    "if days + 1 < recovery_days:",
    "if days + 1 < recovery_days or RECOVERY in prev_raw:")

#: Z-6 回退样本：入闸确认期被移除（危机次日即恢复）
LIQ_PY_NO_CONFIRM = LIQ_PY_GOOD.replace(
    "if days + 1 < confirm_days:", "if False:")
#: 窗口默认值漂移样本
LIQ_PY_CONFIRM_5 = LIQ_PY_GOOD.replace(
    "confirm_days: int = 3", "confirm_days: int = 5")

SCREENING_PY = '''
def screen_stocks_v75(trade_date=None, *,
                      pe_max=paths.V75_DEFAULTS["pe_max"],
                      pb_max=paths.V75_DEFAULTS["pb_max"],
                      roe_min=paths.V75_DEFAULTS["roe_min"],
                      dv_min=paths.V75_DEFAULTS["dv_min"],
                      exclude_bj=True, verbose=False):
    ...
'''


@pytest.fixture()
def four_way(tmp_path):
    root = tmp_path / "chichao"
    (root / "rules").mkdir(parents=True)
    (root / "md_core").mkdir(parents=True)
    (root / "rules" / "single-source.md").write_text(SOURCE_MD, encoding="utf-8")
    (root / "md_core" / "paths.py").write_text(PATHS_PY, encoding="utf-8")
    (root / "md_core" / "screening.py").write_text(SCREENING_PY, encoding="utf-8")
    (root / "md_core" / "market_state.py").write_text(
        MDCORE_STATE_PY, encoding="utf-8")       # X-4 状态机锚点用
    json_path = tmp_path / "rules_mirror_v77.json"
    json_path.write_text(json.dumps(exporter.build_payload(V75, LIQ),
                                    ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return root, json_path


@pytest.fixture()
def btf_root(tmp_path):
    """合成 btf 仓库根（X-4：`btf/data/liq.py` 语义锚点用）。"""
    root = tmp_path / "btf_repo"
    (root / "btf" / "data").mkdir(parents=True)
    (root / "btf" / "data" / "liq.py").write_text(LIQ_PY_GOOD, encoding="utf-8")
    return root


class TestFourWayCheck:
    def test_all_consistent_passes(self, four_way):
        root, json_path = four_way
        assert checker.run_checks(root, json_path) == []
        assert checker.main(["--root", str(root), "--json",
                             str(json_path)]) == checker.EXIT_PASS

    def test_source_drift_detected(self, four_way):
        """① 规则源改数值（ROE>6%）→ 源 vs 镜像/缓存 不一致。"""
        root, json_path = four_way
        md = root / "rules" / "single-source.md"
        md.write_text(SOURCE_MD.replace("ROE>5%", "ROE>6%"), encoding="utf-8")
        fails = checker.run_checks(root, json_path)
        assert any("roe_min" in f for f in fails)
        assert checker.main(["--root", str(root), "--json",
                             str(json_path)]) == checker.EXIT_FAIL

    def test_cache_drift_detected(self, four_way):
        """④ btf 缓存被手改（pe_max=25）→ 镜像 vs 缓存 不一致。"""
        root, json_path = four_way
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        payload["rules"]["L2_filter"]["pe_max"] = 25.0
        json_path.write_text(json.dumps(payload, ensure_ascii=False),
                             encoding="utf-8")
        fails = checker.run_checks(root, json_path)
        assert any(("pe_max" in f and "④" in f) or "pe_max" in f for f in fails)

    def test_unbound_default_detected(self, four_way):
        """③ screening.py 手抄常量（不绑 paths）→ 绑定缺失。"""
        root, json_path = four_way
        (root / "md_core" / "screening.py").write_text(
            'def screen_stocks_v75(*, pe_max=30.0, pb_max=3.0, '
            'roe_min=5.0, dv_min=1.0): ...', encoding="utf-8")
        fails = checker.run_checks(root, json_path)
        assert any("screening.py 未绑定" in f for f in fails)

    def test_missing_file_is_error(self, four_way, tmp_path):
        root, _ = four_way
        missing = tmp_path / "no_such.json"
        assert checker.main(["--root", str(root), "--json",
                             str(missing)]) == checker.EXIT_ERROR


class TestStateMachineAnchor:
    """X-4：状态机语义锚点（阈值数值之外；19 号 §17.3.5 机检缺口）。"""

    def test_consistent_implementation_passes(self, four_way, btf_root):
        root, _ = four_way
        assert checker.run_state_machine_checks(root, btf_root) == []

    def test_self_holding_recovery_detected(self, four_way, btf_root):
        """**X-1 回归守卫**：自我维持子句必须被机检抓出。"""
        root, _ = four_way
        (btf_root / "btf" / "data" / "liq.py").write_text(
            LIQ_PY_SELF_HOLD, encoding="utf-8")
        fails = checker.run_state_machine_checks(root, btf_root)
        assert any("自我维持" in f for f in fails), fails

    def test_missing_window_detected(self, four_way, btf_root):
        root, _ = four_way
        (btf_root / "btf" / "data" / "liq.py").write_text(
            "def _finalize(prev_raw, recovery_days: int = 3, "
            "confirm_days: int = 3): ...", encoding="utf-8")
        fails = checker.run_state_machine_checks(root, btf_root)
        assert any("窗口判定" in f for f in fails), fails

    def test_confirm_window_removed_detected(self, four_way, btf_root):
        """**Z-6 回归守卫**：入闸确认期被移除（危机次日即恢复）必须被抓出。"""
        root, _ = four_way
        (btf_root / "btf" / "data" / "liq.py").write_text(
            LIQ_PY_NO_CONFIRM, encoding="utf-8")
        fails = checker.run_state_machine_checks(root, btf_root)
        assert any("入闸确认期" in f for f in fails), fails

    def test_confirm_days_mismatch_detected(self, four_way, btf_root):
        root, _ = four_way
        (btf_root / "btf" / "data" / "liq.py").write_text(
            LIQ_PY_CONFIRM_5, encoding="utf-8")
        fails = checker.run_state_machine_checks(root, btf_root)
        assert any("confirm_days 默认 5" in f for f in fails), fails

    def test_source_confirm_clause_missing_detected(self, four_way, btf_root):
        """规则源缺「入闸确认期归属」→ ① 锚点报红（Z-6 澄清须可证伪）。"""
        root, _ = four_way
        md = root / "rules" / "single-source.md"
        md.write_text(SOURCE_MD.replace("确认期归属 = CRISIS", "确认期待定"),
                      encoding="utf-8")
        fails = checker.run_state_machine_checks(root, btf_root)
        assert any("Z-6 澄清缺失" in f for f in fails), fails

    def test_recovery_days_mismatch_detected(self, four_way, btf_root):
        root, _ = four_way
        (btf_root / "btf" / "data" / "liq.py").write_text(
            LIQ_PY_GOOD.replace("recovery_days: int = 3",
                                "recovery_days: int = 5"), encoding="utf-8")
        fails = checker.run_state_machine_checks(root, btf_root)
        assert any("≠ 规则源 3 日" in f for f in fails), fails

    def test_source_anchor_missing_detected(self, four_way, btf_root):
        root, _ = four_way
        (root / "rules" / "single-source.md").write_text(
            "# 无 RECOVERY 恢复机制表述\n", encoding="utf-8")
        fails = checker.run_state_machine_checks(root, btf_root)
        assert any("①" in f for f in fails), fails

    def test_real_btf_liq_is_clean(self):
        """真实 `btf/data/liq.py` 代码结构不得含自我维持（常驻锚点）。"""
        real_root = Path(checker.__file__).resolve().parent.parent
        code = checker._code_only(
            (real_root / "btf" / "data" / "liq.py").read_text(encoding="utf-8"))
        assert checker._RE_BTF_SELF_HOLD.search(code) is None
        assert checker._RE_BTF_CONFIRM.search(code) is not None   # Z-6 入闸
        assert checker._RE_BTF_WINDOW.search(code) is not None
        assert checker._RE_BTF_PHASE.search(code) is not None


class TestGrabbers:
    def test_source_values(self, four_way):
        root, _ = four_way
        got = checker.grab_source(root / "rules" / "single-source.md")
        assert got["L2_filter"] == V75
        assert got["L0_LIQ"]["down_crisis"] == 800
        assert got["L0_LIQ"]["small_watch"] == -4.0

    def test_paths_values(self, four_way):
        root, _ = four_way
        got = checker.grab_paths(root / "md_core" / "paths.py")
        assert got["L2_filter"] == V75
        assert got["L0_LIQ"] == LIQ

    def test_screening_bindings(self, four_way):
        root, _ = four_way
        binds = checker.grab_screening(root / "md_core" / "screening.py")
        assert set(binds.values()) == {"pe_max", "pb_max", "roe_min", "dv_min"}
