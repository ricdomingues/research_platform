"""The backtester: statistics that state their sample, and splitting that cannot leak (spec 14, 15, 24)."""

from datetime import timedelta

import pytest

from tests.research.support import BASE, bullish_engulfing_after_decline, zigzag
from virtual_orders.research.backtest import (
    BACKTEST_VERSION,
    SplitName,
    build_occurrences,
    chronological,
    purge,
    summarize,
    time_splits,
    walk_forward,
)
from virtual_orders.research.labels import BarrierResult
from virtual_orders.research.models import Timeframe

from core.domain.models import Direction  # isort: skip
from core.metrics.summary import INSUFFICIENT_SAMPLE  # isort: skip


def occurrences(cycles=4):
    return build_occurrences(zigzag(cycles=cycles), ticker="AAPL", data_as_of=BASE)


def test_an_occurrence_carries_its_features_its_labels_and_its_label_window():
    (found,) = [item for item in build_occurrences(bullish_engulfing_after_decline(), ticker="AAPL",
                                                   data_as_of=BASE)
                if item.pattern == "BULLISH_ENGULFING"]
    assert found.direction is Direction.LONG and found.timeframe is Timeframe.M15
    assert found.features.pattern == "BULLISH_ENGULFING"
    assert found.signal_end_ts == found.detection.end_ts
    assert set(found.horizons) == {5, 10, 20}
    assert found.barrier is not None
    # The label window ends after the signal: it is the only part of an occurrence that reads the future.
    assert found.label_end_ts is not None and found.label_end_ts > found.signal_end_ts
    document = found.as_document()
    assert set(document) >= {"ticker", "pattern", "features", "horizons", "barrier", "label_end_ts"}


def test_a_neutral_pattern_never_becomes_an_occurrence():
    assert all(item.pattern != "DOJI" for item in occurrences())


def test_statistics_count_resolved_barriers_and_never_fold_timeouts_into_losses():
    stats = summarize(occurrences(), horizon=10)
    assert stats.backtest_version == BACKTEST_VERSION
    assert stats.samples >= stats.resolved
    assert stats.resolved == stats.wins + stats.losses
    assert stats.samples == stats.resolved + stats.timeouts + _unresolved(occurrences())
    if stats.resolved:
        assert stats.win_rate is not None and 0 <= stats.win_rate <= 1
    assert stats.avg_return_pct is not None  # the horizon was requested, so returns are reported


def _unresolved(items):
    return sum(1 for item in items
               if item.barrier is None or item.barrier.result is BarrierResult.NO_DATA)


def test_a_small_sample_is_flagged_and_a_short_direction_carries_its_notice():
    few = occurrences()[:5]
    stats = summarize(few)
    assert INSUFFICIENT_SAMPLE in stats.warnings
    if any(item.direction is Direction.SHORT for item in few):
        assert "SHORT_BORROW_NOT_SIMULATED" in stats.warnings


def test_statistics_over_nothing_are_empty_rather_than_zero():
    stats = summarize([])
    assert (stats.samples, stats.resolved, stats.wins) == (0, 0, 0)
    assert (stats.win_rate, stats.expectancy_r, stats.profit_factor) == (None, None, None)
    assert INSUFFICIENT_SAMPLE in stats.warnings


def test_occurrences_are_always_ordered_by_their_signal_candle():
    items = occurrences()
    ordered = chronological(items)
    assert [item.signal_end_ts for item in ordered] == sorted(item.signal_end_ts for item in ordered)
    assert summarize(list(reversed(items))).as_document() == summarize(items).as_document()


# --- splitting ---------------------------------------------------------------------------------------------
def test_the_three_splits_are_chronological_and_do_not_overlap():
    train, validation, test = time_splits(occurrences())
    assert (train.name, validation.name, test.name) == (SplitName.TRAIN, SplitName.VALIDATION, SplitName.TEST)
    assert train.end <= validation.start and validation.end <= test.start
    for split in (train, validation, test):
        for item in split.occurrences:
            assert split.start <= item.signal_end_ts
    assert all(item.signal_end_ts < train.end for item in train.occurrences)
    assert all(train.end <= item.signal_end_ts < validation.end for item in validation.occurrences)


def test_only_the_training_split_is_purged():
    embargo = timedelta(minutes=60)
    train, validation, test = time_splits(occurrences(), embargo=embargo)
    assert train.purged > 0, "the fixture must have label windows crossing the boundary"
    assert (validation.purged, test.purged) == (0, 0)
    # Purging the evaluation sets would not remove a leak, it would remove the evaluation.
    assert validation.samples > 0 and test.samples > 0
    for item in train.occurrences:
        assert item.label_end_ts is None or item.label_end_ts <= train.end - embargo


def test_purge_drops_exactly_the_observations_whose_labels_cross_the_boundary():
    items = chronological(occurrences())
    boundary = items[len(items) // 2].signal_end_ts
    before = [item for item in items if item.signal_end_ts < boundary]
    kept = purge(before, boundary)
    assert all(item.label_end_ts is None or item.label_end_ts <= boundary for item in kept)
    assert len(kept) < len(before)
    # A longer embargo can only remove more, never fewer.
    assert len(purge(before, boundary, embargo=timedelta(hours=2))) <= len(kept)
    with pytest.raises(ValueError, match="embargo must not be negative"):
        purge(before, boundary, embargo=timedelta(seconds=-1))


def test_split_fractions_must_leave_room_for_a_test_period():
    from decimal import Decimal

    items = occurrences()
    with pytest.raises(ValueError, match="leave room for a test period"):
        time_splits(items, train_fraction=Decimal("0.8"), validation_fraction=Decimal("0.2"))
    with pytest.raises(ValueError, match="fractions must be in"):
        time_splits(items, train_fraction=Decimal("0"))
    with pytest.raises(ValueError, match="at least one occurrence"):
        time_splits([])


# --- walk-forward ------------------------------------------------------------------------------------------
def test_walk_forward_trains_only_on_purged_history_before_each_test_window():
    folds = walk_forward(occurrences(), folds=4, embargo=timedelta(minutes=30))
    assert [fold.index for fold in folds] == [1, 2, 3]
    for fold in folds:
        assert fold.train.end == fold.test.start
        for item in fold.train.occurrences:
            assert item.signal_end_ts < fold.test.start
            assert item.label_end_ts is None or item.label_end_ts <= fold.test.start - timedelta(minutes=30)
        for item in fold.test.occurrences:
            assert item.signal_end_ts >= fold.test.start
    # An expanding window: each fold trains on at least as much history as the one before it.
    sizes = [fold.train.samples + fold.train.purged for fold in folds]
    assert sizes == sorted(sizes)


def test_walk_forward_refuses_a_degenerate_request():
    items = occurrences()
    with pytest.raises(ValueError, match="at least 2 folds"):
        walk_forward(items, folds=1)
    with pytest.raises(ValueError, match="not enough occurrences"):
        walk_forward(items[:2], folds=5)


def test_there_is_no_random_split_on_offer():
    """A shuffled split of overlapping label windows is the fastest way to an irreproducible backtest."""
    import virtual_orders.research.backtest as backtest

    assert not [name for name in dir(backtest) if "random" in name.lower() or "shuffle" in name.lower()]
