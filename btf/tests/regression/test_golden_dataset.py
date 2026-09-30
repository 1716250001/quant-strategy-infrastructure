# -*- coding: utf-8 -*-
"""黄金数据集构建器回归（M2 任务 5.6；05 §20.1/§20.2，09 §14.4）。

验收：
    1. **只增不改纪律**：已存在 case —— 一致则 unchanged（不重写）；
       断言漂移 → 拦截报错（GoldenAssertionDrift），仅 --force 可覆盖；
    2. case 结构完整（data/plan/assertions/data_hash + G1 第一断言）；
    3. 构建器可脱离主库（reader 注入）——纪律本身可测；
    4. 真实主库截取（l4，主库不可用时跳过）：G1 三案例数据非空且事件锚定。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import build_golden as bg
import golden_refcalc as ref

SYM = "000001.SZ"
ANCHOR = "20200528"
CASE = "g1_cash_div_same_day"

#: 假数据交易日（覆盖 anchor 前后）
DATES = ["20200526", "20200527", "20200528", "20200529", "20200601",
         "20200602", "20200603", "20200604", "20200605", "20200608"]


def _fake_bars(symbol: str, start: str, end: str, root: Path) -> list[dict]:
    """假 daily：除息日（anchor）价格下调 0.218（=cash_div）。"""
    closes = {"20200528": 13.782, "20200529": 13.782, "20200601": 13.782}
    return [{
        "symbol": symbol, "date": d,
        "open": closes.get(d, 14.0), "high": 14.0, "low": 13.0,
        "close": closes.get(d, 14.0), "pre_close": 14.0,
        "vol": 1e8, "amount": 1e9,
    } for d in DATES]


def _fake_actions(symbol: str, start: str, end: str, root: Path) -> list[dict]:
    return [{"symbol": symbol, "ex_date": ANCHOR, "pay_date": ANCHOR,
             "record_date": None, "cash_div_per_share": 0.218,
             "stk_div_per_share": 0.0, "ann_date": "20200420"}]


def _build(tmp_path: Path, **kw) -> str:
    return bg.build(CASE, tmp_path / "cases", root=Path("fake-root"),
                    bars_reader=_fake_bars, actions_reader=_fake_actions, **kw)


# ═════════════════════════════════════════════════════════════
@pytest.mark.l1
class TestOnlyAddNeverModify:
    """05 §20.1：黄金集只增不改（断言修正=升版）。"""

    def test_create_then_unchanged(self, tmp_path):
        assert _build(tmp_path).endswith("created")
        path = tmp_path / "cases" / f"{CASE}.json"
        before = path.read_text(encoding="utf-8")
        assert _build(tmp_path) == f"{CASE}: unchanged"
        assert path.read_text(encoding="utf-8") == before      # 未被重写

    def test_assertion_drift_is_blocked(self, tmp_path):
        _build(tmp_path)
        path = tmp_path / "cases" / f"{CASE}.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        case["assertions"]["metrics"]["total_return"] = 0.999  # 篡改断言
        path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(bg.GoldenAssertionDrift, match="只增不改"):
            _build(tmp_path)
        # 拦截后磁盘仍是篡改内容（未被静默覆盖）
        assert json.loads(path.read_text(encoding="utf-8"))["assertions"][
            "metrics"]["total_return"] == 0.999

    def test_force_overwrites_with_trace(self, tmp_path):
        _build(tmp_path)
        path = tmp_path / "cases" / f"{CASE}.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        case["assertions"]["metrics"]["total_return"] = 0.999
        path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        assert _build(tmp_path, force=True).startswith(f"{CASE}: updated")
        assert json.loads(path.read_text(encoding="utf-8"))["assertions"][
            "metrics"]["total_return"] != 0.999

    def test_data_change_blocked(self, tmp_path):
        """主库数据变化（data_hash 不同）→ 拦截（须升版而非静默改断言）。"""
        _build(tmp_path)
        path = tmp_path / "cases" / f"{CASE}.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        case["data_hash"] = "sha256:stale"
        path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(bg.GoldenAssertionDrift, match="data_hash"):
            _build(tmp_path)

    def test_verify_does_not_write(self, tmp_path):
        assert "缺失" in _build(tmp_path, verify=True)
        assert not (tmp_path / "cases" / f"{CASE}.json").exists()
        _build(tmp_path)
        assert _build(tmp_path, verify=True) == f"{CASE}: unchanged"

    def test_verify_reports_drift_without_writing(self, tmp_path):
        _build(tmp_path)
        path = tmp_path / "cases" / f"{CASE}.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        case["assertions"]["metrics"]["win_rate"] = 0.123
        path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(bg.GoldenAssertionDrift):
            _build(tmp_path, verify=True)


@pytest.mark.l1
class TestCaseStructure:
    def test_case_fields(self, tmp_path):
        _build(tmp_path)
        case = json.loads((tmp_path / "cases" / f"{CASE}.json").read_text(
            encoding="utf-8"))
        assert case["contract_version"] == "golden.v1"
        assert case["group"] == "G1"
        assert set(case) >= {"data", "plan", "assertions", "data_hash", "source"}
        assert case["data"]["dates"] and case["data"]["bars"]
        assert case["plan"]["targets"][DATES[0]] == {SYM: 0.9}
        # 清仓决策在倒数第二日（卖出落在窗口内）
        assert case["plan"]["targets"][DATES[-2]] == {}

    def test_assertions_cover_g1_first_assertion(self, tmp_path):
        """G1 第一断言=名义入账四式 + 事件调整（09 §14.2）。"""
        _build(tmp_path)
        case = json.loads((tmp_path / "cases" / f"{CASE}.json").read_text(
            encoding="utf-8"))
        a = case["assertions"]
        assert len(a["snapshots"]) == len(case["data"]["dates"])
        assert len(a["fills"]) == 2                      # 买入 + 末日卖出
        assert [e["type"] for e in a["events"]] == ["ex", "pay"]
        # 派息 pay≡ex：窗口事件贡献 Δ=0
        assert a["invariants"]["windows"][0]["window_event_delta"] == pytest.approx(0.0)
        # 除息日 NAV 连续（价格同额下调 → 单日 Δ≈0，仅费用影响）
        nav = {s["date"]: s["total_value"] for s in a["snapshots"]}
        assert nav["20200528"] == pytest.approx(nav["20200527"], abs=1.0)

    def test_assertions_reproducible(self, tmp_path):
        """同数据重算 → 断言逐键一致（R1 前提）。"""
        _build(tmp_path)
        case = json.loads((tmp_path / "cases" / f"{CASE}.json").read_text(
            encoding="utf-8"))
        again = ref.compute_case(case["data"], case["plan"])
        assert again == case["assertions"]


@pytest.mark.l1
class TestCli:
    def test_list_shows_delivered_groups_and_pending(self, capsys):
        """G1–G10 全部交付（规格表内）；pending 为空（不静默假装覆盖）。"""
        bg.main(["--list"])
        out = capsys.readouterr().out
        assert CASE in out
        for group in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9",
                      "G10"):
            assert any(bg.CASE_SPECS[c]["group"] == group for c in bg.CASE_SPECS), group
        tail = out.split("-- pending", 1)
        if len(tail) == 2:                       # 段头仍打印但清单为空
            pending_groups = [line.split()[0] for line in
                              tail[1].strip().splitlines()[1:] if line.strip()]
            assert pending_groups == [], f"应无 pending，实测 {pending_groups}"

    def test_unknown_case_returns_error(self):
        assert bg.main(["--cases", "g9_not_exist"]) == 1

    def test_missing_mainlib_returns_2(self, tmp_path):
        assert bg.main(["--cases", CASE, "--root", str(tmp_path / "nope")]) == 2


# ═════════════════════════════════════════════════════════════
# 真实主库截取（l4；主库不可用时跳过——不静默通过）
# ═════════════════════════════════════════════════════════════
MAINLIB = Path(bg.MARKET_DATA_DIR)
pytestmark_real = pytest.mark.skipif(
    not MAINLIB.is_dir(), reason=f"主库不可访问: {MAINLIB}")


@pytest.mark.l4
@pytestmark_real
class TestRealExtraction:
    def test_extract_g1_cases_from_mainlib(self):
        for case_id in ("g1_cash_div_same_day", "g1_cash_div_ex_ne_pay",
                        "g1_stk_div_mixed"):
            spec = bg.CASE_SPECS[case_id]
            data = bg.extract_case(spec, MAINLIB)
            assert data["dates"], case_id
            assert spec["anchor_ex_date"] in data["dates"], case_id
            actions = [a for a in data["actions"]
                       if a["ex_date"] in data["dates"]]
            assert actions, f"{case_id} 窗口内无公司行动"
            assert all(b["close"] > 0 for b in data["bars"])
            out = ref.compute_case(data, bg.make_plan(spec, data["dates"]))
            assert out["snapshots"]
            assert out["events"], f"{case_id} 事件未触发（持仓未建立？）"

    def test_real_case_assertions_stable(self):
        """真实数据构建两次 → 断言一致（确定性）。"""
        spec = bg.CASE_SPECS[CASE]
        data = bg.extract_case(spec, MAINLIB)
        plan = bg.make_plan(spec, data["dates"])
        assert ref.compute_case(data, plan) == ref.compute_case(data, plan)
