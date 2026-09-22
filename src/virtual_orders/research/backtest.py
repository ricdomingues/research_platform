"""Leak-safe historical evaluation of candlestick setups (Plan 5, D81).

The loop that produces occurrences is the heart of the whole sub-project, so it is written to make a leak
structurally impossible rather than merely unlikely:

* features for the candle at `index` are built from `candles[: index + 1]` only;
* labels for that same candle are built from `candles[index + 1 :]` only;
* the two slices are taken in one place, here, and handed to modules that cannot see the other half;
* the prior trend behind each pattern is computed per pattern length, so a three-candle pattern is never
  described by a context that already contains its own candles.

Splitting follows the same discipline. A random train/test split of a time series is not offered at all: only
chronological train -> validation -> test, and walk-forward folds. Where a label window overlaps the next
period, the overlapping **training** observations are purged and an additional embargo drops those ending just
before the boundary. Evaluation sets are never purged: dropping the observations being judged would not remove
a leak, it would remove the evaluation.

Aggregates are Decimal wherever they are exact. Confidence intervals and the maximum drawdown are resampling
statistics and stay with the platform's existing float implementation in `core.metrics.resampling`, which this
module reuses rather than reimplements.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Direction
from core.metrics.resampling import bootstrap_ci, max_drawdown, mean_stat, win_rate_stat
from core.metrics.summary import INSUFFICIENT_SAMPLE, MIN_SAMPLE, SHORT_BORROW_NOT_SIMULATED
from virtual_orders.research.candlesticks import DEFAULT_THRESHOLDS, PatternThresholds, PriorContext, detect_at
from virtual_orders.research.features import FeatureSnapshot, build_snapshot
from virtual_orders.research.indicators import IndicatorSeries, compute_series
from virtual_orders.research.labels import (
    DEFAULT_HORIZONS,
    DEFAULT_STOP_ATR,
    DEFAULT_TARGET_ATR,
    BarrierOutcome,
    BarrierResult,
    HorizonOutcome,
    atr_barriers,
    fixed_horizon,
    target_before_stop,
)
from virtual_orders.research.market_structure import (
    PRIOR_TREND_LOOKBACK,
    SwingPoint,
    prior_trend,
    structure_at,
    swing_points,
)
from virtual_orders.research.models import (
    ZERO,
    Candle,
    PatternDetection,
    PatternDirection,
    Timeframe,
    quantize_ratio,
    quantized,
)

BACKTEST_VERSION = "backtest-v1"
R_QUANTUM = Decimal("0.0001")
PCT_QUANTUM = Decimal("0.0001")
DEFAULT_MAX_CANDLES = 20
PATTERN_LENGTHS = (1, 2, 3)


class SplitName(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"


@dataclass(frozen=True)
class Occurrence:
    """One historical candidate: what was known at the candle, and what happened after it."""

    ticker: str
    timeframe: Timeframe
    pattern: str
    direction: Direction
    signal_ts: datetime
    signal_end_ts: datetime
    label_end_ts: datetime | None
    features: FeatureSnapshot
    detection: PatternDetection
    horizons: dict[int, HorizonOutcome]
    barrier: BarrierOutcome | None

    @property
    def r_multiple(self) -> Decimal | None:
        return None if self.barrier is None else self.barrier.r_multiple

    def as_document(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker, "timeframe": self.timeframe, "pattern": self.pattern,
            "direction": self.direction, "signal_ts": self.signal_ts, "signal_end_ts": self.signal_end_ts,
            "label_end_ts": self.label_end_ts, "features": self.features.as_document(),
            "horizons": {str(horizon): outcome.as_document() for horizon, outcome in sorted(self.horizons.items())},
            "barrier": None if self.barrier is None else self.barrier.as_document(),
        }


def direction_of(detection: PatternDetection) -> Direction | None:
    """A neutral pattern (a plain Doji) is an observation, never a tradable direction."""
    if detection.direction is PatternDirection.BULLISH:
        return Direction.LONG
    if detection.direction is PatternDirection.BEARISH:
        return Direction.SHORT
    return None


def prior_context(
    candles: Sequence[Candle], index: int, *, lookback: int = PRIOR_TREND_LOOKBACK
) -> PriorContext:
    """The prior trend before the first candle of a 1-, 2- and 3-candle pattern ending at `index`."""
    return PriorContext({
        length: prior_trend(candles, index - (length - 1), lookback=lookback) for length in PATTERN_LENGTHS
    })


def build_occurrences(
    candles: Sequence[Candle],
    *,
    ticker: str,
    data_as_of: datetime,
    series: IndicatorSeries | None = None,
    swings: Sequence[SwingPoint] | None = None,
    thresholds: PatternThresholds = DEFAULT_THRESHOLDS,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    max_candles: int = DEFAULT_MAX_CANDLES,
    target_atr: Decimal = DEFAULT_TARGET_ATR,
    stop_atr: Decimal = DEFAULT_STOP_ATR,
    prior_lookback: int = PRIOR_TREND_LOOKBACK,
    patterns: Sequence[str] | None = None,
) -> list[Occurrence]:
    """Every detection in `candles`, each with its own leak-free features and forward-looking labels.

    The entry convention is deliberate and stated: a candidate is entered at the **close of the candle that
    completed the pattern**, the first price actually available to someone reading that pattern. The barriers
    are ATR multiples of that entry and the label walks forward from the *next* candle only.
    """
    indicators = compute_series(candles) if series is None else series
    pivots = list(swing_points(candles) if swings is None else swings)
    wanted = None if patterns is None else set(patterns)
    occurrences: list[Occurrence] = []
    for index, candle in enumerate(candles):
        history = candles[: index + 1]  # everything the engine may look at
        future = candles[index + 1 :]  # everything only the label may look at
        detections = detect_at(
            history, index, prior=prior_context(candles, index, lookback=prior_lookback), thresholds=thresholds
        )
        if not detections:
            continue
        structure = structure_at(history, index, indicators, swings=pivots)
        atr_value = indicators.atr_at(index)
        for detection in detections:
            if wanted is not None and detection.pattern not in wanted:
                continue
            direction = direction_of(detection)
            if direction is None:
                continue
            entry = candle.close
            barrier: BarrierOutcome | None = None
            if atr_value is not None and atr_value > ZERO and entry > ZERO:
                stop, target = atr_barriers(entry, atr_value, direction, target_atr=target_atr, stop_atr=stop_atr)
                barrier = target_before_stop(future, entry=entry, stop=stop, target=target, direction=direction,
                                             max_candles=max_candles)
            outcomes = {
                horizon: fixed_horizon(future, entry=entry, direction=direction, horizon=horizon)
                for horizon in horizons
            }
            used = max([outcome.candles_used for outcome in outcomes.values()]
                       + [0 if barrier is None else barrier.candles_used])
            label_end = future[used - 1].end_ts if 0 < used <= len(future) else None
            occurrences.append(Occurrence(
                ticker=ticker, timeframe=candle.timeframe, pattern=detection.pattern, direction=direction,
                signal_ts=candle.ts, signal_end_ts=candle.end_ts, label_end_ts=label_end,
                features=build_snapshot(list(history), index, ticker=ticker, data_as_of=data_as_of,
                                        series=indicators, structure=structure, detection=detection),
                detection=detection, horizons=outcomes, barrier=barrier,
            ))
    return occurrences


@dataclass(frozen=True)
class BacktestStats:
    """Sample-count-first statistics. Every rate is reported beside the count it was computed from."""

    backtest_version: str
    samples: int
    resolved: int
    wins: int
    losses: int
    timeouts: int
    ambiguous: int
    gap_through: int
    win_rate: Decimal | None
    avg_return_pct: Decimal | None
    median_return_pct: Decimal | None
    expectancy_r: Decimal | None
    avg_r: Decimal | None
    profit_factor: Decimal | None
    max_drawdown_r: float
    avg_mfe_r: Decimal | None
    avg_mae_r: Decimal | None
    win_rate_ci: tuple[float, float] | None
    expectancy_ci: tuple[float, float] | None
    warnings: tuple[str, ...]

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def _mean(values: Sequence[Decimal], quantum: Decimal) -> Decimal | None:
    if not values:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return quantized(sum(values, ZERO) / len(values), quantum)


def _median(values: Sequence[Decimal], quantum: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return quantized(ordered[middle], quantum)
    with localcontext(CANONICAL_CONTEXT):
        return quantized((ordered[middle - 1] + ordered[middle]) / 2, quantum)


def chronological(occurrences: Sequence[Occurrence]) -> list[Occurrence]:
    """One stable order for every statistic and split: by the signal candle, then ticker, then pattern."""
    return sorted(occurrences, key=lambda item: (item.signal_end_ts, item.ticker, item.pattern))


def summarize(
    occurrences: Sequence[Occurrence],
    *,
    horizon: int | None = None,
    resamples: int = 2000,
    seed: int = 42,
) -> BacktestStats:
    """Aggregate barrier outcomes (and, when `horizon` is given, that horizon's returns) over `occurrences`.

    Wins and losses count only *resolved* barriers: a timeout is neither and is reported on its own, so a win
    rate can never be inflated by quietly dropping the candidates that never reached a barrier.
    """
    ordered = chronological(occurrences)
    barriers = [item.barrier for item in ordered if item.barrier is not None]
    resolved = [item for item in barriers if item.result in (BarrierResult.TARGET, BarrierResult.STOP)]
    wins = [item for item in resolved if item.result is BarrierResult.TARGET]
    losses = [item for item in resolved if item.result is BarrierResult.STOP]
    r_values = [item.r_multiple for item in barriers if item.r_multiple is not None]
    returns = ([outcome.return_pct for item in ordered
                if (outcome := item.horizons.get(horizon)) is not None and outcome.return_pct is not None]
               if horizon is not None else [])
    floats = [float(value) for value in r_values]
    positive = [value for value in r_values if value > ZERO]
    negative = [value for value in r_values if value < ZERO]

    warnings: list[str] = []
    if len(ordered) < MIN_SAMPLE:
        warnings.append(INSUFFICIENT_SAMPLE)
    if any(item.direction is Direction.SHORT for item in ordered):
        warnings.append(SHORT_BORROW_NOT_SIMULATED)

    with localcontext(CANONICAL_CONTEXT):
        win_rate = quantize_ratio(Decimal(len(wins)) / len(resolved)) if resolved else None
        total_losses = sum(negative, ZERO)
        profit_factor = quantize_ratio(sum(positive, ZERO) / abs(total_losses)) if total_losses != ZERO else None
    return BacktestStats(
        backtest_version=BACKTEST_VERSION,
        samples=len(ordered),
        resolved=len(resolved),
        wins=len(wins),
        losses=len(losses),
        timeouts=sum(1 for item in barriers if item.result is BarrierResult.TIMEOUT),
        ambiguous=sum(1 for item in barriers if item.ambiguous),
        gap_through=sum(1 for item in barriers if item.gap_through),
        win_rate=win_rate,
        avg_return_pct=_mean(returns, PCT_QUANTUM),
        median_return_pct=_median(returns, PCT_QUANTUM),
        expectancy_r=_mean(r_values, R_QUANTUM),
        avg_r=_mean(r_values, R_QUANTUM),
        profit_factor=profit_factor,
        max_drawdown_r=max_drawdown(floats),
        avg_mfe_r=_mean([item.mfe_r for item in barriers if item.mfe_r is not None], R_QUANTUM),
        avg_mae_r=_mean([item.mae_r for item in barriers if item.mae_r is not None], R_QUANTUM),
        win_rate_ci=bootstrap_ci(floats, win_rate_stat, resamples, seed),
        expectancy_ci=bootstrap_ci(floats, mean_stat, resamples, seed),
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class Split:
    name: SplitName
    start: datetime
    end: datetime
    occurrences: tuple[Occurrence, ...]
    purged: int

    @property
    def samples(self) -> int:
        return len(self.occurrences)


def purge(
    train: Sequence[Occurrence], boundary: datetime, *, embargo: timedelta = timedelta(0)
) -> list[Occurrence]:
    """Drop **training** observations whose label window reaches into (or within `embargo` of) the next period.

    An observation signalled before the boundary but labelled from candles the next period also contains would
    otherwise let training learn from the very data the next period is meant to judge blind. Only the training
    side is ever purged: removing observations from the evaluation side would shrink the evaluation, not the
    leak.
    """
    if embargo < timedelta(0):
        raise ValueError("embargo must not be negative")
    cutoff = boundary - embargo
    return [item for item in train if item.label_end_ts is None or item.label_end_ts <= cutoff]


def _boundary(ordered: Sequence[Occurrence], fraction: Decimal) -> datetime:
    index = min(len(ordered) - 1, max(0, int(len(ordered) * float(fraction))))
    return ordered[index].signal_end_ts


def time_splits(
    occurrences: Sequence[Occurrence],
    *,
    train_fraction: Decimal = Decimal("0.6"),
    validation_fraction: Decimal = Decimal("0.2"),
    embargo: timedelta = timedelta(0),
) -> tuple[Split, Split, Split]:
    """Chronological train -> validation -> test.

    The training split is purged and embargoed at its boundary; validation and test keep every observation that
    falls inside them. There is no random-split option on purpose: shuffling a time series with overlapping
    label windows is the fastest way to a backtest that cannot be reproduced out of sample.
    """
    if not ZERO < train_fraction < 1 or not ZERO < validation_fraction < 1:
        raise ValueError("fractions must be in (0, 1)")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train and validation must leave room for a test period")
    ordered = chronological(occurrences)
    if not ordered:
        raise ValueError("a split needs at least one occurrence")
    first, last = ordered[0].signal_end_ts, ordered[-1].signal_end_ts
    train_end = _boundary(ordered, train_fraction)
    validation_end = _boundary(ordered, train_fraction + validation_fraction)
    train = [item for item in ordered if item.signal_end_ts < train_end]
    validation = [item for item in ordered if train_end <= item.signal_end_ts < validation_end]
    test = [item for item in ordered if item.signal_end_ts >= validation_end]
    kept = purge(train, train_end, embargo=embargo)
    return (
        Split(SplitName.TRAIN, first, train_end, tuple(kept), len(train) - len(kept)),
        Split(SplitName.VALIDATION, train_end, validation_end, tuple(validation), 0),
        Split(SplitName.TEST, validation_end, last, tuple(test), 0),
    )


@dataclass(frozen=True)
class Fold:
    index: int
    train: Split
    test: Split


def walk_forward(
    occurrences: Sequence[Occurrence], *, folds: int, embargo: timedelta = timedelta(0)
) -> list[Fold]:
    """Expanding-window walk-forward: every fold trains on all purged history before its test window.

    Fold *k* tests on the *(k+1)*-th chronological slice and trains on everything before it. An expanding
    window is used rather than a rolling one because the samples a candlestick study produces are small; the
    choice is stated here so it is never inferred from the results. A fold whose training set is emptied by
    purging is still returned, with `samples == 0`, rather than silently dropped.
    """
    if folds < 2:
        raise ValueError("walk-forward needs at least 2 folds")
    ordered = chronological(occurrences)
    if len(ordered) < folds:
        raise ValueError("not enough occurrences for the requested number of folds")
    size = len(ordered) // folds
    result: list[Fold] = []
    for fold in range(1, folds):
        boundary = ordered[fold * size].signal_end_ts
        end = ordered[min((fold + 1) * size, len(ordered)) - 1].signal_end_ts
        history = [item for item in ordered if item.signal_end_ts < boundary]
        kept = purge(history, boundary, embargo=embargo)
        test = [item for item in ordered if boundary <= item.signal_end_ts <= end]
        result.append(Fold(
            fold,
            Split(SplitName.TRAIN, ordered[0].signal_end_ts, boundary, tuple(kept), len(history) - len(kept)),
            Split(SplitName.TEST, boundary, end, tuple(test), 0),
        ))
    return result
