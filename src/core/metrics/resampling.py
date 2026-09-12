"""Resampling statistics for strategy metrics (spec 5.4). Seeds make results reproducible."""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

Statistic = Callable[[np.ndarray], np.ndarray]


def max_drawdown(values: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def win_rate_stat(samples: np.ndarray) -> np.ndarray:
    return (samples > 0).mean(axis=1)


def mean_stat(samples: np.ndarray) -> np.ndarray:
    return samples.mean(axis=1)


def bootstrap_ci(
    values: Sequence[float], statistic: Statistic, resamples: int, seed: int, level: float = 0.95
) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(data), size=(resamples, len(data)))
    stats = statistic(data[indexes])
    tail = (1.0 - level) / 2.0 * 100.0
    low, high = np.percentile(stats, [tail, 100.0 - tail])
    return float(low), float(high)


def drawdown_permutation_percentiles(
    values: Sequence[float], resamples: int, seed: int
) -> dict[str, float] | None:
    if len(values) < 2:
        return None
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    permutations = rng.permuted(np.tile(data, (resamples, 1)), axis=1)
    equity = np.concatenate([np.zeros((resamples, 1)), np.cumsum(permutations, axis=1)], axis=1)
    drawdowns = (np.maximum.accumulate(equity, axis=1) - equity).max(axis=1)
    p5, p50, p95 = np.percentile(drawdowns, [5, 50, 95])
    return {"p5": float(p5), "p50": float(p50), "p95": float(p95)}
