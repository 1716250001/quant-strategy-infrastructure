# -*- coding: utf-8 -*-
"""D4-A 契约测试（REQ-20261121-06）

覆盖：
  ① D3 §8.4-1：`mark_partial` 能撤销磁盘旧 `completed`（下次仍重拉）；
  ② D3 §8.4-2：合并后 `status` 按实际 partial 重算，不残留 partial；
     `failed` / `source_empty` 显式态保留；
  ③ F1：`run_fund_nav_update` 真实返回契约（dict），交易日失败 → fail>0，
     且 `cmd_fund_nav` 真实消费后 rc=1（不再 monkeypatch 函数本身造假 dict）；
  ④ F2：`cmd_download_full` / `cmd_download_cb` / `cmd_backfill` 消费底层
     真实契约（fail>0 → rc=1）。
"""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

import fetch.fund_nav_update as fnu
from cli import fetch_cmds
from fetch.base import Checkpoint


# ============================================================
# ① D3 §8.4-1：partial 撤销磁盘旧 completed
# ============================================================
def test_mark_partial_overrides_disk_completed(tmp_path):
    path = tmp_path / ".checkpoint.json"
    path.write_text(json.dumps({"daily": {"completed": ["600519.SH"]}}), encoding="utf-8")

    cp = Checkpoint(data_root=str(tmp_path))
    assert cp.is_done("daily", "600519.SH")
    cp.mark_partial("daily", "600519.SH")
    cp.save()

    data = json.loads(path.read_text(encoding="utf-8"))["daily"]
    assert "600519.SH" not in data["completed"]
    assert data["partial"] == ["600519.SH"]
    assert "600519.SH" in data.get("partial_ts", {})

    # 重新加载后仍是 partial ⇒ 下次视为未完成、重拉
    cp2 = Checkpoint(data_root=str(tmp_path))
    assert cp2.is_partial("daily", "600519.SH")
    assert not cp2.is_done("daily", "600519.SH")


def test_mark_done_after_partial_wins(tmp_path):
    """partial 之后正常完成 → completed 时间戳更新，partial 被撤销。"""
    path = tmp_path / ".checkpoint.json"
    path.write_text(json.dumps({"daily": {"completed": ["600519.SH"]}}), encoding="utf-8")

    cp = Checkpoint(data_root=str(tmp_path))
    cp.mark_partial("daily", "600519.SH")
    cp.save()

    cp2 = Checkpoint(data_root=str(tmp_path))
    cp2.mark_done("daily", "600519.SH")
    cp2.save()

    data = json.loads(path.read_text(encoding="utf-8"))["daily"]
    assert data["completed"] == ["600519.SH"]
    assert data.get("partial", []) == []


# ============================================================
# ② D3 §8.4-2：status 重算 / 显式态保留
# ============================================================
def test_status_not_stale_partial_after_merge(tmp_path):
    path = tmp_path / ".checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "daily": {
                    "completed": ["600519.SH"],
                    "partial": [],
                    "status": "partial",
                    "status_ts": "2026-11-21T18:00:00.000000",
                }
            }
        ),
        encoding="utf-8",
    )

    cp = Checkpoint(data_root=str(tmp_path))
    cp.save()  # 合并后 partial=[] ⇒ status 应重算

    data = json.loads(path.read_text(encoding="utf-8"))["daily"]
    assert data["status"] == "done", "partial 为空时不得残留 status=partial"
    assert Checkpoint(data_root=str(tmp_path)).get_status("daily") == "done"


@pytest.mark.parametrize("status", ["failed", "source_empty"])
def test_explicit_status_preserved(tmp_path, status):
    path = tmp_path / ".checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "daily": {
                    "completed": [],
                    "status": status,
                    "status_ts": "2026-11-21T18:00:00.000000",
                }
            }
        ),
        encoding="utf-8",
    )

    cp = Checkpoint(data_root=str(tmp_path))
    cp.save()

    data = json.loads(path.read_text(encoding="utf-8"))["daily"]
    assert data["status"] == status


def test_explicit_failed_can_be_recovered_by_newer_done(tmp_path):
    path = tmp_path / ".checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "daily": {
                    "completed": [],
                    "status": "failed",
                    "status_ts": "2026-11-21T18:00:00.000000",
                }
            }
        ),
        encoding="utf-8",
    )

    cp = Checkpoint(data_root=str(tmp_path))
    cp.mark_done("daily", "600519.SH")
    cp.save()

    data = json.loads(path.read_text(encoding="utf-8"))["daily"]
    assert data["status"] == "done"


# ============================================================
# ③ F1：fund-nav 真实返回契约 + CLI 集成
# ============================================================
def _stub_nav_env(monkeypatch, tmp_path, by_date):
    """只替换外部源端与路径；`run_fund_nav_update` 本体保持真实。"""
    monkeypatch.setattr(fnu, "FUND_NAV_DIR", str(tmp_path / "fund_nav"))
    monkeypatch.setattr(fnu, "META_DIR", str(tmp_path / "meta"))
    monkeypatch.setattr(
        fnu, "get_recent_trade_dates", lambda n, end_date=None: ["20261119", "20261120"]
    )
    monkeypatch.setattr(fnu, "get_pro", lambda: object())
    monkeypatch.setattr(fnu, "load_of_scope", lambda: None)
    monkeypatch.setattr(fnu, "fetch_nav_by_date", by_date)


def test_fund_nav_real_contract_failed_day(monkeypatch, tmp_path):
    ok_df = pd.DataFrame(
        {
            "ts_code": ["000001.OF"],
            "nav_date": ["20261120"],
            "unit_nav": [1.0],
            "accum_nav": [1.0],
        }
    )

    def by_date(pro, limiter, nav_date, max_pages=20):
        if nav_date == "20261119":
            return None, 1, False, False  # 首页失败
        return ok_df, 1, False, True

    _stub_nav_env(monkeypatch, tmp_path, by_date)
    result = fnu.run_fund_nav_update(dry_run=True, lookback_days=2)

    assert isinstance(result, dict)
    assert result["fail"] == 1
    assert result["failed_days"] == ["20261119"]
    assert result["day_status"]["20261119"] == "failed"
    assert result["day_status"]["20261120"] == "ok"

    args = SimpleNamespace(
        repair=False, repair_threshold=5, dry_run=True, limit=None, date=None, days=2
    )
    assert fetch_cmds.cmd_fund_nav(args) == 1


def test_fund_nav_real_contract_truncated_day(monkeypatch, tmp_path):
    ok_df = pd.DataFrame(
        {
            "ts_code": ["000001.OF"],
            "nav_date": ["20261119"],
            "unit_nav": [1.0],
            "accum_nav": [1.0],
        }
    )

    def by_date(pro, limiter, nav_date, max_pages=20):
        return ok_df, 20, True, True  # 分页触顶 → partial

    _stub_nav_env(monkeypatch, tmp_path, by_date)
    result = fnu.run_fund_nav_update(dry_run=True, lookback_days=2)

    assert result["fail"] == 2  # 两个交易日都触顶
    assert result["partial_days"] == ["20261119", "20261120"]
    assert result["day_status"]["20261119"] == "partial"


def test_fund_nav_repair_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(fnu, "FUND_NAV_DIR", str(tmp_path / "fund_nav"))
    monkeypatch.setattr(fnu, "META_DIR", str(tmp_path / "meta"))
    monkeypatch.setattr(fnu, "load_of_scope", lambda: None)
    import common.reader as reader

    monkeypatch.setattr(reader, "iter_year_files", lambda name: [])

    result = fnu.repair_incomplete(dry_run=False, limit=None)
    assert result["fail"] == 0 and result["targets"] == 0


# ============================================================
# ④ F2：download-* / backfill 失败数透传
# ============================================================
def test_download_full_fail_rc1(monkeypatch):
    monkeypatch.setattr(
        "fetch.full_download.run_download", lambda **kw: {"fail": 2, "apis": {"daily": 2}}
    )
    args = SimpleNamespace(phase="daily", reset=False, dry_run=False, workers=None)
    assert fetch_cmds.cmd_download_full(args) == 1


def test_download_cb_fail_rc1(monkeypatch):
    monkeypatch.setattr(
        "fetch.cb_download.run_cb_download",
        lambda **kw: {"fail": 1, "ok": 0, "empty": 0, "skipped": 0, "dry_run": False},
    )
    args = SimpleNamespace(reset=False, dry_run=False, workers=None)
    assert fetch_cmds.cmd_download_cb(args) == 1


def test_fund_nav_repair_partition_fail_rc1(monkeypatch, tmp_path):
    """R2：repair 的 upsert_by_year 分区失败 → fail=1 → cmd_fund_nav rc=1。"""
    import common.reader as reader

    p = tmp_path / "2026.parquet"
    pd.DataFrame({"ts_code": ["000001.OF"]}).to_parquet(p, index=False)
    monkeypatch.setattr(fnu, "FUND_NAV_DIR", str(tmp_path / "fund_nav"))
    monkeypatch.setattr(fnu, "META_DIR", str(tmp_path / "meta"))
    monkeypatch.setattr(fnu, "load_of_scope", lambda: None)
    monkeypatch.setattr(fnu, "get_pro", lambda: object())
    monkeypatch.setattr(
        fnu, "RateLimiter", lambda **k: SimpleNamespace(total_requests=0, status_str=lambda: "")
    )
    monkeypatch.setattr(
        fnu,
        "fetch_nav_full_history",
        lambda *a, **k: pd.DataFrame({"ts_code": ["000001.OF"], "nav_date": ["20260102"]}),
    )
    monkeypatch.setattr(
        fnu, "upsert_by_year", lambda *a, **k: {"added": 0, "fail": 1, "partitions": 1, "rows": 1}
    )
    monkeypatch.setattr(reader, "iter_year_files", lambda name: [("2026", str(p))])

    result = fnu.repair_incomplete(dry_run=False, limit=None)
    assert result["fail"] == 1
    assert result["targets"] == 1

    args = SimpleNamespace(
        repair=True, repair_threshold=5, dry_run=False, limit=None, date=None, days=5
    )
    assert fetch_cmds.cmd_fund_nav(args) == 1


def test_backfill_real_target_fail_rc1(monkeypatch, tmp_path):
    """R1：真实 run_target + 桩源端 None → fail>0 → cmd_backfill rc=1。"""
    import fetch.backfill as bf

    monkeypatch.setattr(bf, "MARKET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bf, "BACKFILL_DIR_ROOT", str(tmp_path))
    monkeypatch.setattr(bf, "get_pro", lambda: object())
    monkeypatch.setattr(bf, "ts_call_with_retry", lambda *a, **k: None)

    result = bf.run_backfill(
        tiers=("A",), only=["fut_basic"], dry_run=False, workers=1, parallel_targets=1
    )
    assert result["fail"] >= 1, "run_target 的取数失败必须上抛到 run_backfill"

    args = SimpleNamespace(
        list_targets=False,
        tier="A",
        only="fut_basic",
        dry_run=False,
        workers=1,
        parallel_targets=1,
        limit=None,
        force=False,
        codes_file=None,
        force_listed=False,
    )
    assert fetch_cmds.cmd_backfill(args) == 1


def test_backfill_by_date_paged_truncated_marks_partial(monkeypatch, tmp_path):
    """O3：by_date 分页触顶全链路 → partial，不 mark_done。"""
    import fetch.backfill as bf

    monkeypatch.setattr(bf, "BACKFILL_DIR_ROOT", str(tmp_path))
    df = pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20260102"]})
    monkeypatch.setattr(bf, "fetch_paged_by_date", lambda *a, **k: (df, 5, True))

    cp = Checkpoint(data_root=str(tmp_path))
    from config.daily import DAILY_UPDATE_EXTRA

    conf = dict(DAILY_UPDATE_EXTRA["daily"])
    r = bf.run_target(
        object(),
        "daily",
        conf,
        cp,
        workers=1,
        dry_run=False,
        date_override=["20260102"],
        ignore_checkpoint=True,
    )

    assert r["fail"] == 0  # 截断可重拉，不计取数/落盘失败
    assert cp.is_partial("daily", "20260102")
    assert not cp.is_done("daily", "20260102")


def test_backfill_dry_run_reports_contract():
    """真实 run_backfill 的 dry-run 返回值是 dict（不触网、只读目标清单）。"""
    from fetch.backfill import run_backfill

    result = run_backfill(tiers=("X",), only=None, dry_run=True)
    assert isinstance(result, dict) and result["fail"] == 0
