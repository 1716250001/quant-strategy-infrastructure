# -*- coding: utf-8 -*-
"""L1 单测：RC-16（2026-10-05）蒙特卡洛 / 置换检验工具。

锚点（09 #12）：**同种子重跑逐位一致**；产物含 p05/p50/p95；双侧 p 值；
边界不伪造（不可算 ⇒ NaN/None，不填 0）。
"""
from __future__ import annotations

import pytest
from btf.analytics.bootstrap import (
    annualized_return,
    annualized_sharpe,
    bootstrap_ci,
    distribution_summary,
    max_drawdown,
    noise_baseline,
    percentile,
    permutation_pvalue,
)

pytestmark = [pytest.mark.l1]

R = [0.004, -0.002, 0.006, -0.001, 0.003, 0.005, -0.004, 0.002,
     0.001, -0.003, 0.007, 0.0, -0.002, 0.004, 0.003]


class TestStatistics:
    def test_annualized_sharpe_known_case(self):
        # mean=0.02, 样本 std=0.01 ⇒ 2×√252
        got = annualized_sharpe([0.01, 0.02, 0.03], annualization=252)
        assert got == pytest.approx(2 * 252 ** 0.5, rel=1e-12)

    def test_sharpe_degenerate_is_nan_not_zero(self):
        assert annualized_sharpe([0.01]) != annualized_sharpe([0.01])   # NaN
        assert annualized_sharpe([0.01, 0.01, 0.01]) != \
            annualized_sharpe([0.01, 0.01, 0.01])                       # std=0 ⇒ NaN

    def test_max_drawdown_known_path(self):
        # nav: 1.10 → 0.88 → 0.924；峰值 1.10 ⇒ mdd = −20%
        assert max_drawdown([0.1, -0.2, 0.05]) == pytest.approx(-0.2, rel=1e-12)

    def test_annualized_return_geometric(self):
        # 10 个 1% ⇒ 几何年化 = 1.01^(252/10 × 10) − 1 = 1.01^252 − 1
        got = annualized_return([0.01] * 10, annualization=252)
        assert got == pytest.approx(1.01 ** 252 - 1.0, rel=1e-12)

    def test_percentile_nearest_rank(self):
        assert percentile([1, 2, 3, 4, 5], 0.5) == 3
        assert percentile([1], 0.05) == 1

    def test_distribution_summary_shape(self):
        s = distribution_summary([1.0, 2.0, 3.0, 4.0])
        assert s["n"] == 4 and s["min"] == 1.0 and s["max"] == 4.0
        # 最近秩口径：idx = ceil(0.5×4)−1 = 1 ⇒ 2.0（与 percentile 同源）
        assert s["p50"] == 2.0
        assert s["p05"] == 1.0 and s["p95"] == 4.0


class TestDeterminism:
    def test_bootstrap_same_seed_identical(self):
        a = bootstrap_ci(R, n_resamples=200, seed=7)
        b = bootstrap_ci(R, n_resamples=200, seed=7)
        assert a == b

    def test_permutation_same_seed_identical(self):
        a = permutation_pvalue(R, n_permutations=200, seed=7)
        b = permutation_pvalue(R, n_permutations=200, seed=7)
        assert a == b

    def test_noise_same_seed_identical(self):
        pool = [x / 100.0 for x in range(-50, 51)]
        a = noise_baseline(pool, n_hold=10, observed=0.5, n_draws=100, seed=3)
        b = noise_baseline(pool, n_hold=10, observed=0.5, n_draws=100, seed=3)
        assert a == b

    def test_different_seed_differs(self):
        a = bootstrap_ci(R, n_resamples=200, seed=1)
        b = bootstrap_ci(R, n_resamples=200, seed=2)
        assert a["sharpe"]["p05"] != b["sharpe"]["p05"]


class TestBootstrapCi:
    def test_shape_and_crosses_zero(self):
        out = bootstrap_ci(R, n_resamples=200, seed=0)
        for key in ("sharpe", "annualized_return"):
            item = out[key]
            for k in ("observed", "p05", "p50", "p95", "ci_low", "ci_high"):
                assert k in item
            assert item["n_valid"] == 200
        assert isinstance(out["sharpe"]["crosses_zero"], bool)

    def test_strong_positive_no_cross_zero(self):
        strong = [0.01, 0.012, 0.009, 0.011, 0.013, 0.008, 0.012, 0.010]
        out = bootstrap_ci(strong, n_resamples=200, seed=0)
        assert out["sharpe"]["crosses_zero"] is False


class TestPermutation:
    def test_p_value_bounds(self):
        out = permutation_pvalue(R, scheme="sign", statistic="sharpe",
                                 n_permutations=300, seed=0)
        assert 0.0 <= out["p_value"] <= 1.0
        assert out["null_summary"]["n"] == 300

    def test_order_scheme_for_drawdown(self):
        out = permutation_pvalue([0.05, -0.1, 0.04, -0.02, 0.03],
                                 scheme="order", statistic="max_drawdown",
                                 n_permutations=200, seed=0)
        assert out["observed"] <= 0.0
        assert 0.0 <= out["p_value"] <= 1.0

    def test_constant_series_nan_observed(self):
        out = permutation_pvalue([0.0] * 10, scheme="sign",
                                 statistic="sharpe", n_permutations=50)
        assert out["observed"] != out["observed"]      # NaN
        assert out["p_value"] is None                  # 不可算 ⇒ None（不伪造）

    def test_unknown_statistic_rejected(self):
        with pytest.raises(ValueError, match="statistic"):
            permutation_pvalue(R, statistic="magic")


class TestNoiseBaseline:
    def test_percentile_extremes(self):
        pool = [x / 1000.0 for x in range(-100, 101)]
        high = noise_baseline(pool, n_hold=20, observed=99.0, n_draws=200,
                              seed=0)
        assert high["observed_percentile"] == 1.0
        low = noise_baseline(pool, n_hold=20, observed=-99.0, n_draws=200,
                             seed=0)
        assert low["observed_percentile"] == 0.0

    def test_empty_pool_rejected(self):
        with pytest.raises(ValueError, match="pool"):
            noise_baseline([], n_hold=5, observed=0.0)
