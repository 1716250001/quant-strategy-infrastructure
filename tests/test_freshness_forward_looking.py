# -*- coding: utf-8 -*-
"""§462 F-2 / §465 F-6/F-7/F-8：前置日历豁免、总数同源、豁免垫底。"""
import tools.db.freshness as f


def test_forward_looking_set_is_exactly_12_registered_tables():
    """F-2：白名单恰为 12 张，新增/删除必须显式改这里。"""
    assert f.FORWARD_LOOKING == {
        "fut_trade_cal", "cn_schedule", "broker_recommend",
        "macro_uk_cpi", "macro_jp_cpi", "macro_de_cpi", "macro_ca_cpi",
        "macro_au_cpi", "macro_ca_gdp", "macro_uk_unemp", "macro_jp_unemp",
        "macro_au_unemp",
    }


def test_ds_negative_whitelisted_is_normal_not_unknown():
    """F-2：白名单表 ds<0 判「正常」；非白名单未来数据不得伪装正常。"""
    rows = f.build_rows()
    for r in rows:
        if r["ds"] is None or r["ds"] >= 0:
            continue
        if r["table"] in f.FORWARD_LOOKING:
            assert r["verdict"] == "正常", r
            assert r["note"] in ("前置日历", "静态名录"), r
        else:
            assert r["verdict"] != "正常", f"未登记未来数据不得判正常: {r}"


def test_forward_exemption_yields_to_more_specific_verdicts(monkeypatch):
    """F-8：豁免垫底 —— 停更/静态名录必须能推翻「前置日历」。"""
    monkeypatch.setitem(f.STALE_BY_DESIGN, "fut_trade_cal",
                        {"since": "2026-01-01", "why": "test"})
    rows = {r["table"]: r for r in f.build_rows()}
    assert rows["fut_trade_cal"]["verdict"] == "已停更"

    # 清空白名单后，静态名录表仍应按 STATIC_TABLES 判「正常/静态名录」，不得掉成「未知」
    monkeypatch.setattr(f, "FORWARD_LOOKING", frozenset())
    rows2 = {r["table"]: r for r in f.build_rows()}
    assert rows2["cn_schedule"]["verdict"] == "正常"
    assert rows2["cn_schedule"]["note"] == "静态名录"


def test_total_registered_matches_build_rows_full_scan():
    """F-7：零扫盘总数必须与全量 build_rows(None) 同源。"""
    assert f.total_registered() == len(f.build_rows(None))
