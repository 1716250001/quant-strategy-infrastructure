# -*- coding: utf-8 -*-
"""L1 单测：推断层（**RC-06，2026-10-05**）——PSR / DSR / 夏普标准误。

含**结论锚点**（派单指定，可证伪）：
    零信号随机收益 × 432 组参数网格 → 挑出的"最佳"策略
    **PSR 判显著（>0.95）、DSR 判不显著（<0.95）**
    ⇒ 「试得越多越不可信」被数字化（本机实测：PSR 0.9994 / DSR 0.5964）。

纪律（ADR-13）：本模块只输出数字，**不判定"策略有效/无效"**。
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from btf.analytics.inference import (
    annualized_to_period,
    deflated_sharpe,
    dsr_from_returns,
    expected_max_sharpe,
    probabilistic_sharpe,
    psr_from_returns,
    sharpe_moments,
    sharpe_stderr,
)

pytestmark = [pytest.mark.l1]

SEED = 20261005
N_TRIALS = 432
N_PERIODS = 500
SIGMA = 0.01


class TestNormalHelpers:
    def test_ppf_matches_known_quantiles(self):
        from btf.analytics.inference import _norm_cdf, _norm_ppf

        assert _norm_ppf(0.975) == pytest.approx(1.959963985, abs=1e-6)
        assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
        assert _norm_cdf(0.0) == pytest.approx(0.5)
        assert _norm_cdf(_norm_ppf(0.9)) == pytest.approx(0.9, abs=1e-8)

    def test_ppf_domain_guard(self):
        from btf.analytics.inference import _norm_ppf

        with pytest.raises(ValueError):
            _norm_ppf(0.0)
        with pytest.raises(ValueError):
            _norm_ppf(1.0)


class TestSharpeStderr:
    def test_zero_sr_normal_case(self):
        """SR=0、正态 ⇒ SE = 1/√(n−1)（教科书式）。"""
        assert sharpe_stderr(0.0, n_periods=N_PERIODS) == pytest.approx(
            1 / math.sqrt(N_PERIODS - 1))

    def test_positive_skew_shrinks_stderr(self):
        base = sharpe_stderr(0.1, n_periods=N_PERIODS, skew=0.0, kurt=3.0)
        assert sharpe_stderr(0.1, n_periods=N_PERIODS, skew=0.5,
                             kurt=3.0) < base
        assert sharpe_stderr(0.1, n_periods=N_PERIODS, skew=-0.5,
                             kurt=3.0) > base

    def test_fat_tails_inflate_stderr(self):
        assert sharpe_stderr(0.1, n_periods=N_PERIODS, kurt=6.0) > \
            sharpe_stderr(0.1, n_periods=N_PERIODS)

    def test_period_guard(self):
        with pytest.raises(ValueError):
            sharpe_stderr(0.1, n_periods=1)


class TestProbabilisticSharpe:
    def test_monotone_in_sr_and_periods(self):
        low = probabilistic_sharpe(0.05, n_periods=N_PERIODS)
        high = probabilistic_sharpe(0.15, n_periods=N_PERIODS)
        assert high > low
        assert (probabilistic_sharpe(0.1, n_periods=1000)
                > probabilistic_sharpe(0.1, n_periods=100))

    def test_zero_sr_gives_half(self):
        assert probabilistic_sharpe(0.0, sr0=0.0,
                                    n_periods=N_PERIODS) == pytest.approx(0.5)

    def test_annualized_conversion(self):
        assert annualized_to_period(0.1 * math.sqrt(252)) == pytest.approx(0.1)
        with pytest.raises(ValueError):
            annualized_to_period(0.1, annualization=0)


class TestDeflatedSharpe:
    def test_n1_equals_psr(self):
        assert deflated_sharpe(0.1, n_periods=N_PERIODS, n_trials=1) \
            == pytest.approx(probabilistic_sharpe(0.1, sr0=0.0,
                                                  n_periods=N_PERIODS))

    def test_decreases_with_n_trials(self):
        values = [deflated_sharpe(0.1, n_periods=N_PERIODS, n_trials=n)
                  for n in (1, 10, 100, 1000)]
        assert values == sorted(values, reverse=True)

    def test_expected_max_sharpe_positive_and_grows(self):
        assert expected_max_sharpe(n_periods=N_PERIODS, n_trials=1) == 0.0
        a = expected_max_sharpe(n_periods=N_PERIODS, n_trials=10)
        b = expected_max_sharpe(n_periods=N_PERIODS, n_trials=1000)
        assert 0 < a < b


class TestMoments:
    def test_gaussian_moments(self):
        rng = np.random.default_rng(SEED)
        x = rng.normal(0.0, SIGMA, 20_000)
        n, sr, skew, kurt = sharpe_moments(x)
        assert n == 20_000
        assert sr == pytest.approx(0.0, abs=0.05)
        assert skew == pytest.approx(0.0, abs=0.1)
        assert kurt == pytest.approx(3.0, abs=0.3)

    def test_sample_guard(self):
        with pytest.raises(ValueError):
            sharpe_moments([0.01, 0.02])
        with pytest.raises(ValueError):
            sharpe_moments([0.01, 0.01, 0.01])       # 方差 0


@pytest.fixture(scope="module")
def best_zero_signal() -> np.ndarray:
    """432 组零信号随机收益中**单期夏普最高**的那条（固定种子 = 可复现）。"""
    rng = np.random.default_rng(SEED)
    series = [rng.normal(0.0, SIGMA, N_PERIODS) for _ in range(N_TRIALS)]
    srs = [float(s.mean() / s.std(ddof=1)) for s in series]
    return series[max(range(N_TRIALS), key=lambda k: srs[k])]


class TestZeroSignalAnchor:
    """**结论锚点**：432 组零信号 → 挑出的"最佳" PSR 显著 / DSR 不显著。"""

    def test_psr_significant_dsr_not(self, best_zero_signal):
        psr = psr_from_returns(list(best_zero_signal))
        dsr = dsr_from_returns(list(best_zero_signal), n_trials=N_TRIALS)
        assert psr > 0.95, f"零信号最优的单策略显著性应「显著」（实测 {psr:.4f}）"
        assert dsr < 0.95, f"多重检验校正后应**不显著**（实测 {dsr:.4f}）"
        assert dsr < psr

    def test_dsr_penalty_shrinks_with_total_trials(self, best_zero_signal):
        """把"历史累计试验数"也算进 N ⇒ DSR 进一步下降（越试越不可信）。"""
        dsr_432 = dsr_from_returns(list(best_zero_signal), n_trials=N_TRIALS)
        dsr_4320 = dsr_from_returns(list(best_zero_signal),
                                    n_trials=N_TRIALS * 10)
        assert dsr_4320 < dsr_432
