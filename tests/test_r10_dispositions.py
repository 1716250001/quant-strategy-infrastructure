# -*- coding: utf-8 -*-
"""REQ-20261126-01 T7/T8：R10 扩面（by_code 归宿）与承载方实证。

T7：每张 by_code 表必须有归宿（日更组/周期组/豁免/排班/EXCLUDED），否则红。
T8：EXCLUDED 里“由 X 通道承载”的条目，X 的代码必须真实引用该表。
"""

from config import (
    BACKFILL_TARGETS,
    DAILY_UPDATE_EXEMPT,
    DAILY_UPDATE_EXTRA,
    DAILY_UPDATE_GROUPS,
    PERIODIC_UPDATE_TARGETS,
)
from config.schedule import EXCLUDED, SCHEDULE
from tools.regress_pipeline import (
    CODE_DIR,
    check_excluded_carriers,
    r10_dispositions,
)


def test_real_config_has_no_orphans():
    """现盘配置：by_code 表全部有归宿，且 fund_share 已进入候选（by_date）。"""
    cand, orphans = r10_dispositions(
        BACKFILL_TARGETS,
        DAILY_UPDATE_EXTRA,
        DAILY_UPDATE_GROUPS,
        DAILY_UPDATE_EXEMPT,
        PERIODIC_UPDATE_TARGETS,
        SCHEDULE,
        EXCLUDED,
    )
    assert not orphans, f"现盘出现无归宿表: {orphans}"
    assert cand["fund_share"]["mode"] == "by_date"
    # by_code 代表项确实进入候选（证明扩面生效，不是只扫 by_date）
    assert cand["dividend"]["mode"] == "by_code"
    assert cand["fund_nav"]["mode"] == "by_code"


def test_negative_orphan_by_code_table():
    """负例：新增一张 by_code 表但任何通道都没登记 → 必须报出来。"""
    targets = {"orphan_tbl": {"mode": "by_code", "tier": "B"}}
    _cand, orphans = r10_dispositions(targets, {}, {}, {}, {}, {}, {})
    assert orphans == ["orphan_tbl(tier=B,mode=by_code)"], orphans


def test_each_disposition_suppresses_orphan():
    conf = {"orphan_tbl": {"mode": "by_code", "tier": "B"}}
    cases = [
        {"groups": {"core": ["orphan_tbl"]}},
        {"exempt": {"orphan_tbl": "理由"}},
        {"periodic": {"orphan_tbl": {"reason": "理由"}}},
        {"schedule": {"orphan_tbl": "W"}},
        {"excluded": {"orphan_tbl": "静态名录"}},
    ]
    for case in cases:
        _cand, orphans = r10_dispositions(
            conf,
            {},
            case.get("groups", {}),
            case.get("exempt", {}),
            case.get("periodic", {}),
            case.get("schedule", {}),
            case.get("excluded", {}),
        )
        assert not orphans, f"{case} 应作为合法归宿，却仍报 {orphans}"


def test_empty_excluded_reason_is_not_disposition():
    """R5：EXCLUDED 理由为空不算归宿（{"x": ""} 必须报孤儿）。"""
    targets = {"orphan_tbl": {"mode": "by_code", "tier": "B"}}
    _cand, orphans = r10_dispositions(targets, {}, {}, {}, {}, {}, {"orphan_tbl": ""})
    assert orphans == ["orphan_tbl(tier=B,mode=by_code)"], orphans


def test_none_excluded_reason_is_not_disposition():
    """S3：理由为 None 视为空（str(None)='None' 不得冒充有理由）。"""
    targets = {"orphan_tbl": {"mode": "by_code", "tier": "B"}}
    _cand, orphans = r10_dispositions(targets, {}, {}, {}, {}, {}, {"orphan_tbl": None})
    assert orphans == ["orphan_tbl(tier=B,mode=by_code)"], orphans


def test_real_excluded_reasons_nonempty():
    """R5：现盘 EXCLUDED 每一条都必须写明理由。"""
    empty = [k for k, v in EXCLUDED.items() if v is None or not str(v).strip()]
    assert not empty, f"EXCLUDED 理由为空: {empty}"


def test_real_excluded_carriers_pass():
    """现盘：fund_nav 的承载方 fund_nav_update.py 真实引用该表。"""
    assert "fund_nav" in EXCLUDED and "承载" in EXCLUDED["fund_nav"]
    assert check_excluded_carriers(EXCLUDED, CODE_DIR) == []


def test_carrier_negative_cases(tmp_path):
    """负例：未登记路径 / 文件缺失 / 文件未引用表名 → 都必须报出。"""
    assert check_excluded_carriers({"x": "由 y 通道承载"}, str(tmp_path)) == ["x(未登记承载方路径)"]

    import tools.regress_pipeline as rp

    old = dict(rp.EXCLUDED_CARRIER_CHECKS)
    try:
        rp.EXCLUDED_CARRIER_CHECKS["missing"] = "fetch/no_such_file.py"
        assert "missing(承载方文件不存在 fetch/no_such_file.py)" in check_excluded_carriers(
            {"missing": "由 X 承载"}, str(tmp_path)
        )

        (tmp_path / "carrier.py").write_text("# 无关内容\n", encoding="utf-8")
        rp.EXCLUDED_CARRIER_CHECKS["no_ref"] = "carrier.py"
        assert "no_ref(carrier.py 未引用该表)" in check_excluded_carriers(
            {"no_ref": "由 X 承载"}, str(tmp_path)
        )

        (tmp_path / "carrier.py").write_text("# no_ref 在本文件被引用\n", encoding="utf-8")
        assert check_excluded_carriers({"no_ref": "由 X 承载"}, str(tmp_path)) == []
    finally:
        rp.EXCLUDED_CARRIER_CHECKS.clear()
        rp.EXCLUDED_CARRIER_CHECKS.update(old)
