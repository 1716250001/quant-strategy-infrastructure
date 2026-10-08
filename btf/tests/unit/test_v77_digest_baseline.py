# -*- coding: utf-8 -*-
"""**R37′（N43，19 号 §97.9）**：v77 三档 digest 基线文件结构守卫。

基线真源＝`tools/v77_digest_baseline.json`（新口径冻结值 ＋ 旧值留痕）；
逐档复跑校验见 `tools/check_v77_digest.py`（不进单测——三档全跑属 L4 级）。
"""
from __future__ import annotations

import json
import pathlib

BASELINE = (pathlib.Path(__file__).resolve().parents[2]
            / "tools" / "v77_digest_baseline.json")


def test_baseline_shape_and_old_values_kept():
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert data["schema"] == "v77-three-tier-digest-baseline/v1"
    assert set(data["baselines"]) == {"preRC", "delivered", "stress"}
    for prof, ref in data["baselines"].items():
        assert ref["metrics_digest"].startswith("sha256:")
        for k in ("excess", "ir", "total_return", "n_fills", "n_days"):
            assert k in ref, f"{prof} 缺 {k}"
    # 旧值留痕（N43：口径变更理由与旧 digest 必须可追溯）
    assert set(data["previous_baseline"]) == {
        "era", "preRC", "delivered", "stress"}
    assert data["previous_baseline"]["era"].startswith("2e8f933")
    # 新旧必须不同（旧＝全样本口径；新＝在场日口径——N43 根因）
    for prof in ("preRC", "delivered", "stress"):
        assert (data["baselines"][prof]["metrics_digest"]
                != data["previous_baseline"][prof])
    assert "在场日口径" in data["reason"]
