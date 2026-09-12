import pytest

from core.metrics.resampling import (
    bootstrap_ci, drawdown_permutation_percentiles, max_drawdown, mean_stat, win_rate_stat,
)

SAMPLE = [1.0, -1.0, 2.0, -0.5]


def test_max_drawdown():
    assert max_drawdown(SAMPLE) == pytest.approx(1.0)
    assert max_drawdown([]) == 0.0
    assert max_drawdown([-1.0, -2.0]) == pytest.approx(3.0)
    assert max_drawdown([1.0, 2.0]) == 0.0


def test_bootstrap_ci_is_reproducible_and_bounded():
    first = bootstrap_ci(SAMPLE, mean_stat, 2000, 42)
    assert first == bootstrap_ci(SAMPLE, mean_stat, 2000, 42)
    low, high = first
    assert -1.0 <= low <= 0.375 <= high <= 2.0
    assert bootstrap_ci([0.5] * 10, mean_stat, 500, 1) == (pytest.approx(0.5), pytest.approx(0.5))
    assert bootstrap_ci([1.0, 2.0, 3.0], win_rate_stat, 500, 1) == (1.0, 1.0)
    assert bootstrap_ci([1.0], mean_stat, 500, 1) is None


def test_drawdown_permutation_percentiles():
    result = drawdown_permutation_percentiles(SAMPLE, 2000, 42)
    assert result == drawdown_permutation_percentiles(SAMPLE, 2000, 42)
    assert set(result) == {"p5", "p50", "p95"}
    assert 0.0 <= result["p5"] <= result["p50"] <= result["p95"] <= 1.5
    assert drawdown_permutation_percentiles([1.0, 2.0, 3.0], 200, 7) == {"p5": 0.0, "p50": 0.0, "p95": 0.0}
    assert drawdown_permutation_percentiles([1.0], 200, 7) is None
