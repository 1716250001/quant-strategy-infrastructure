# -*- coding: utf-8 -*-
"""L2 集成：**§172 B（19 号）** `is_suspended` 口径修正——R／盘中停牌放行。

反向三例（真实主库样本；对应 §172 G#2/G#3/G#4）：
- `000007.SZ@20240702`（`R` 复牌）⇒ **False**
- `000005.SZ@20240306`（`S` 且 `timing` 空）⇒ **True**
- `000996.SZ@20240606`（`S` 且 `timing` 非空）⇒ **False**

外加口径对齐（G#1）：`state._suspensions` 与 `quality._full_day_suspensions`
对同一窗口给出**逐对相同**的全日停牌集合。
"""
from __future__ import annotations

import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.state import StateSynthesizer
from btf.domain.types import TradingDate

pytestmark = [pytest.mark.l2]

requires_mainlib = pytest.mark.skipif(
    not (MARKET_DATA_DIR / "suspend_d").is_dir(),
    reason=f"主库不可用: {MARKET_DATA_DIR}")

#: 2024 年样本（真实记录，见派出单 §172 G）
R_DAY = ("000007.SZ", "20240702")
S_FULL_DAY = ("000005.SZ", "20240306")
S_INTRADAY = ("000996.SZ", "20240606")


@pytest.fixture(scope="module")
def synth() -> StateSynthesizer:
    return StateSynthesizer(MARKET_DATA_DIR)


def _suspended(synth: StateSynthesizer, sym: str, ymd: str) -> bool:
    panel = synth.states(TradingDate.from_ymd(ymd), [sym])
    return bool(panel[sym].is_suspended)


@requires_mainlib
def test_r_relisting_is_not_suspended(synth):
    """G#2：`R`（复牌）记录**不再**判为停牌。"""
    sym, day = R_DAY
    assert _suspended(synth, sym, day) is False


@requires_mainlib
def test_full_day_suspension_still_suspended(synth):
    """G#3：`S` 且 `suspend_timing` 为空 ⇒ 仍不可交易。"""
    sym, day = S_FULL_DAY
    assert _suspended(synth, sym, day) is True


@requires_mainlib
def test_intraday_suspension_is_tradable(synth):
    """G#4：`S` 且 `timing` 非空（盘中停牌）⇒ 放行（口径扩大部分）。"""
    sym, day = S_INTRADAY
    assert _suspended(synth, sym, day) is False


@requires_mainlib
def test_state_and_quality_alignment(synth):
    """G#1：两处口径逐对相同（`S` 且无 `timing`）。"""
    from btf.data.core import YearTableStore
    from btf.data.quality import _full_day_suspensions

    syms = {R_DAY[0], S_FULL_DAY[0], S_INTRADAY[0]}
    quality_set = _full_day_suspensions(
        YearTableStore(MARKET_DATA_DIR), "20240101", "20241231", syms)
    state_set = {(c, d) for (c, d) in synth._suspensions(2024)
                 if c in syms}
    assert state_set == quality_set
    assert (S_FULL_DAY[0], S_FULL_DAY[1]) in state_set
    assert R_DAY not in state_set
    assert S_INTRADAY not in state_set
