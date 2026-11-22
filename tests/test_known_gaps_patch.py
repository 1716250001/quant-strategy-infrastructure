# -*- coding: utf-8 -*-
"""G2 尾单 T4：tools/db/known_gaps_patch.py 的干跑/备份/合并/校验口径。"""

import json

import pytest

from tools.db.known_gaps_patch import run


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _patch_file(tmp_path, patch):
    p = tmp_path / "patch.json"
    _write_json(p, {"patch": patch})
    return p


def _range(code, start, end, keys=2):
    return {"code": code, "from": start, "to": end, "keys": keys}


def test_dry_run_does_not_write(tmp_path):
    kg = tmp_path / "known_gaps.json"
    _write_json(kg, {"_comment": "基线", "fund_adj": {"A": ["1"], "note": "旧"}})
    before = kg.read_bytes()
    patch = _patch_file(
        tmp_path,
        {
            "fund_adj": {
                "source_empty_ranges": [_range("159001.SZ", "20160104", "20160104")],
                "source_empty_evidence": {
                    "159001.SZ": {
                        "probe_rows": 0,
                        "probe_date": "20261122",
                        "probe_source": "tushare fund_adj(ts_code,trade_date)",
                    }
                },
                "note": "新增说明",
                "real_gap_backfill": [
                    {"code": "184688.SZ", "keys": 191, "from": "19990104", "to": "19991022"}
                ],
            },
        },
    )

    result = run(patch, kg_path=str(kg), apply=False)

    assert kg.read_bytes() == before
    assert result["applied"] is False
    assert result["backup"] is None
    s = result["stats"]["fund_adj"]
    assert (s["added_ranges"], s["added_keys"], s["deduped"]) == (1, 2, 0)
    assert s["real_gap_keys"] == 191


def test_apply_backs_up_and_preserves_existing_fields(tmp_path):
    kg = tmp_path / "known_gaps.json"
    _write_json(
        kg,
        {
            "_comment": "基线",
            "fund_adj": {
                "A": ["159001.SZ"],
                "B": [],
                "source_empty": ["159001.SZ"],
                "source_empty_evidence": {
                    "159001.SZ": {"probe_rows": 9, "probe_date": "20260101"},
                    "160422.SZ": {"probe_rows": 0, "probe_date": "20261029"},
                },
                "note": "既有说明",
                "source_empty_count": 1,
            },
        },
    )
    before = kg.read_bytes()
    patch = _patch_file(
        tmp_path,
        {
            "fund_adj": {
                "source_empty_ranges": [_range("159001.SZ", "20160101", "20160105", 3)],
                "source_empty_evidence": {
                    "159001.SZ": {"probe_rows": 0, "probe_date": "20261122"},
                    "160422.SZ": {"probe_rows": 0, "probe_date": "20261122"},
                    "184688.SZ": {"probe_rows": 0, "probe_date": "20261122"},
                },
                "note": "补丁说明",
                "real_gap_backfill": [
                    {"code": "184688.SZ", "keys": 191, "from": "19990104", "to": "19991022"}
                ],
            },
        },
    )

    result = run(patch, kg_path=str(kg), apply=True)

    backups = list(tmp_path.glob(kg.name + ".bak-known-gaps-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before
    assert result["backup"] == str(backups[0])

    merged = json.loads(kg.read_text(encoding="utf-8"))
    t = merged["fund_adj"]
    assert t["A"] == ["159001.SZ"] and t["B"] == []
    assert t["source_empty"] == ["159001.SZ"]
    assert t["source_empty_count"] == 1
    assert t["source_empty_ranges"] == [_range("159001.SZ", "20160101", "20160105", 3)]
    assert t["source_empty_evidence"]["159001.SZ"]["probe_rows"] == 9
    assert t["source_empty_evidence"]["160422.SZ"]["probe_date"] == "20261029"
    assert t["source_empty_evidence"]["184688.SZ"]["probe_rows"] == 0
    assert "既有说明" in t["note"] and "补丁说明" in t["note"]
    assert t["real_gap_backfill"][0]["code"] == "184688.SZ"


def test_duplicate_ranges_are_idempotent(tmp_path):
    kg = tmp_path / "known_gaps.json"
    _write_json(kg, {"_comment": "基线"})
    r = _range("600018.SH", "20010508", "20060925", 1261)
    patch = _patch_file(tmp_path, {"adj_factor": {"source_empty_ranges": [r, dict(r)]}})

    first = run(patch, kg_path=str(kg), apply=True)
    assert first["stats"]["adj_factor"]["added_ranges"] == 1
    assert first["stats"]["adj_factor"]["deduped"] == 1

    second = run(patch, kg_path=str(kg), apply=True)
    assert second["stats"]["adj_factor"]["added_ranges"] == 0
    assert second["stats"]["adj_factor"]["deduped"] == 2

    merged = json.loads(kg.read_text(encoding="utf-8"))
    assert len(merged["adj_factor"]["source_empty_ranges"]) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"code": "60018.SH", "from": "20010508", "to": "20060925", "keys": 1},
        {"code": "600018.XX", "from": "20010508", "to": "20060925", "keys": 1},
        {"code": "600018.SH", "from": "2001058", "to": "20060925", "keys": 1},
        {"code": "600018.SH", "from": "20011301", "to": "20060925", "keys": 1},
        {"code": "600018.SH", "from": "20010508", "to": "20060925", "keys": 0},
    ],
)
def test_invalid_code_or_date_rejected(tmp_path, bad):
    kg = tmp_path / "known_gaps.json"
    _write_json(kg, {"_comment": "基线"})
    before = kg.read_bytes()
    patch = _patch_file(tmp_path, {"adj_factor": {"source_empty_ranges": [bad]}})

    with pytest.raises(ValueError):
        run(patch, kg_path=str(kg), apply=True)
    assert kg.read_bytes() == before


def test_unknown_table_rejected(tmp_path):
    kg = tmp_path / "known_gaps.json"
    _write_json(kg, {"_comment": "基线"})
    patch = _patch_file(tmp_path, {"not_a_table": {"source_empty_ranges": []}})
    with pytest.raises(ValueError):
        run(patch, kg_path=str(kg), apply=False)


def test_real_gap_backfill_not_in_source_empty_ranges(tmp_path):
    kg = tmp_path / "known_gaps.json"
    _write_json(kg, {"_comment": "基线"})
    patch = _patch_file(
        tmp_path,
        {
            "daily_basic": {
                "source_empty_ranges": [_range("000001.SZ", "19910403", "19910506", 2)],
                "real_gap_backfill": [
                    {"code": "000022.SZ", "keys": 5180, "from": "19930505", "to": "20141231"},
                    {"code": "000043.SZ", "keys": 4774, "from": "19940928", "to": "20141231"},
                ],
            },
        },
    )

    run(patch, kg_path=str(kg), apply=True)
    t = json.loads(kg.read_text(encoding="utf-8"))["daily_basic"]
    empty_codes = {r["code"] for r in t["source_empty_ranges"]}
    real_codes = {r["code"] for r in t["real_gap_backfill"]}
    assert empty_codes == {"000001.SZ"}
    assert real_codes == {"000022.SZ", "000043.SZ"}
    assert empty_codes.isdisjoint(real_codes)
