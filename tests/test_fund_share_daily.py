# -*- coding: utf-8 -*-
"""REQ-20261126-01 F2-A：fund_share 日更主链修复（T1–T4 + 断点域迁移）。

覆盖：
  ① T1/T2/T3 配置：by_date 形态、分页参数、extend 组、断点 date8 域；
  ② T4 行为：by_date 落盘前按 conf["market_filter"] 剔除 .OF（仅 SH/SZ 落盘）；
  ③ T4 反例：未配 market_filter 的表逐字不变（fund_daily 等零影响）。

背景（只读审查结论）：fund_share 此前**无任何日常增量通道**，唯一写入口
by_code backfill 又被「断点 completed + 库内已有代码」双闸门锁死 ⇒ 2,271 只中
1,761 只停在 20260918。本单改为 by_date + extend 日更，并更正 config/schedule.py
里“由 fund-nav 承载”的错误前提。
"""

import pandas as pd

import fetch.backfill as bf
from config.backfill import BACKFILL_PAGE_CONF
from config.backfill_targets import BACKFILL_TARGETS
from config.checkpoint_registry import domains_for
from config.daily import DAILY_UPDATE_GROUPS
from config.schedule import EXCLUDED
from fetch.base import Checkpoint
from fetch.daily_update import engine_supports_daily


# ============================================================
# ① 配置契约（T1/T2/T3 + 断点域）
# ============================================================
def test_fund_share_config_is_by_date():
    conf = BACKFILL_TARGETS["fund_share"]
    assert conf["mode"] == "by_date", "by_code 会被断点/库内已有双闸门锁死，必须 by_date"
    assert conf["date_param"] == "trade_date"
    assert conf["date_col"] == "trade_date"
    assert conf["subset"] == ["ts_code", "trade_date"]
    assert conf["start"] == "19980327", "start 须与 SOURCE_START_BASELINE 一致"
    assert conf["max_rows"] == 5000
    assert conf["market_filter"] == ["SH", "SZ"], "本表维持场内口径，.OF 不入表"
    assert "codes" not in conf, "by_date 形态不应再依赖 fund_etf 代码白名单"


def test_fund_share_page_conf():
    """T2：服务端单页硬顶 3,000（20260930 实测 26,640 行 = 9 页）。"""
    assert BACKFILL_PAGE_CONF["fund_share"] == (3000, 12)


def test_fund_share_in_daily_extend_group_and_not_excluded():
    """T3/T5：由 extend 日更组承载；EXCLUDED 的错误前提条目已删除。"""
    assert "fund_share" in DAILY_UPDATE_GROUPS["extend"]
    assert engine_supports_daily(BACKFILL_TARGETS["fund_share"]) is True
    assert "fund_share" not in EXCLUDED


def test_fund_share_checkpoint_domain_is_date8():
    """断点域迁移：date8 域必须有 PRIMARY，否则 mark_done 会按“未登记表”拒写。"""
    doms = domains_for("fund_share", library="market_data", key_space="date8")
    assert doms, "fund_share 改 by_date 后必须在 .backfill_checkpoint.json 的 date8 域登记"
    assert any(d.authority == "PRIMARY" for d in doms), "date8 域应为 PRIMARY"


# ============================================================
# ② T4 行为：market_filter 生效
# ============================================================
def _run_by_date(monkeypatch, tmp_path, conf, df):
    monkeypatch.setattr(bf, "BACKFILL_DIR_ROOT", str(tmp_path))
    monkeypatch.setattr(bf, "build_tasks", lambda *_a, **_k: (["20261127"], "date"))
    monkeypatch.setattr(bf, "fetch_paged_by_date", lambda *_a, **_k: (df, 1, False))
    cp = Checkpoint(data_root=str(tmp_path))
    return bf.run_target(object(), "fund_share", conf, cp, workers=1, dry_run=False)


def test_market_filter_drops_of_rows(monkeypatch, tmp_path):
    """含 .OF / 7 位分级码的合成 df → 仅 6 位 SH/SZ 落盘（T11 硬化）。"""
    df = pd.DataFrame(
        {
            "ts_code": ["510300.SH", "159922.SZ", "000001.OF", "510301.OF", "1500011.SZ"],
            "trade_date": ["20261127"] * 5,
            "fd_share": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )
    conf = dict(BACKFILL_TARGETS["fund_share"])
    r = _run_by_date(monkeypatch, tmp_path, conf, df)
    assert r["done"] == 1, "该日应落盘成功"

    out = pd.read_parquet(tmp_path / "fund_share" / "2026.parquet")
    assert set(out["ts_code"]) == {"510300.SH", "159922.SZ"}, "非 SH/SZ 码不得落盘"


def test_fund_share_source_empty_dates_registered():
    """T12：B1 探针的 1,536 个源端空日已登记（计数/证据/floor 自洽）。"""
    import json
    import os

    from tools.regress_pipeline import SOURCE_EMPTY_DATES_FLOOR

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "tools", "db", "data", "known_gaps.json")
    with open(path, encoding="utf-8") as f:
        kg = json.load(f)
    fs = kg["fund_share"]
    assert len(fs["source_empty_dates"]) == fs["source_empty_dates_count"] == 1536
    assert fs["source_empty_dates_evidence"]["nonempty_rows"] == 0
    assert fs["source_empty_dates_evidence"]["empty_count"] == 1536
    assert SOURCE_EMPTY_DATES_FLOOR["fund_share"] == 1536


def test_without_market_filter_keeps_all_rows(monkeypatch, tmp_path):
    """反例：未配 market_filter 的表不做后缀过滤（fund_daily 等行为逐字不变）。"""
    assert "market_filter" not in BACKFILL_TARGETS["fund_daily"], (
        "fund_daily 走的是 market=O 专用护栏，不应用通用 market_filter"
    )
    df = pd.DataFrame(
        {
            "ts_code": ["510300.SH", "000001.OF"],
            "trade_date": ["20261127"] * 2,
            "fd_share": [1.0, 2.0],
        }
    )
    conf = dict(BACKFILL_TARGETS["fund_share"])
    conf.pop("market_filter")
    _run_by_date(monkeypatch, tmp_path, conf, df)

    out = pd.read_parquet(tmp_path / "fund_share" / "2026.parquet")
    assert set(out["ts_code"]) == {"510300.SH", "000001.OF"}, "未配 market_filter 时不得过滤任何码"
