"""Outcome labels (spec 24): target first, stop first, neither, both in one candle, gaps and missing bars.

The same-candle case is the one that decides whether a backtest is honest. The intrabar path is unknown, so the
stop wins — the same convention `fill_model v1` uses for real virtual orders — and the outcome is flagged
`ambiguous` so the share of results resting on that convention can always be counted.
"""

import pytest

from tests.research.support import D, candle
from virtual_orders.research.labels import (
    AMBIGUITY_POLICY,
    LABEL_VERSION,
    BarrierResult,
    atr_barriers,
    fixed_horizon,
    target_before_stop,
)
from virtual_orders.research.models import Candle  # noqa: F401  (documents the type the labellers consume)

from core.domain.models import Direction  # isort: skip

ENTRY, STOP, TARGET = D(100), D(99), D(102)


def outcome(future, direction=Direction.LONG, entry=ENTRY, stop=STOP, target=TARGET, max_candles=5):
    return target_before_stop(future, entry=entry, stop=stop, target=target, direction=direction,
                              max_candles=max_candles)


def test_the_target_is_reached_first():
    found = outcome([candle(1, 100, "102.5", "99.5", 102)])
    assert (found.result, found.exit_price, found.r_multiple) == (BarrierResult.TARGET, TARGET, D("2.0000"))
    assert (found.candles_to_outcome, found.ambiguous, found.gap_through) == (1, False, False)
    assert found.target_first is True
    assert (found.label_version, found.ambiguity_policy) == (LABEL_VERSION, AMBIGUITY_POLICY)


def test_the_stop_is_reached_first():
    found = outcome([candle(1, 100, "100.5", "98.5", 99)])
    assert (found.result, found.exit_price, found.r_multiple) == (BarrierResult.STOP, STOP, D("-1.0000"))
    assert found.target_first is False


def test_one_candle_containing_both_barriers_resolves_to_the_stop_and_is_flagged():
    found = outcome([candle(1, 100, 103, 98, 101)])
    assert found.result is BarrierResult.STOP
    assert found.ambiguous is True  # the share of results resting on the convention stays countable
    assert found.r_multiple == D("-1.0000")


def test_a_gap_through_a_barrier_fills_at_the_open_not_at_the_barrier():
    through_stop = outcome([candle(1, 97, "97.5", 96, "96.5")])
    assert (through_stop.result, through_stop.exit_price) == (BarrierResult.STOP, D(97))
    assert (through_stop.gap_through, through_stop.r_multiple) == (True, D("-3.0000"))
    through_target = outcome([candle(1, 103, 104, "102.5", "103.5")])
    assert (through_target.result, through_target.exit_price) == (BarrierResult.TARGET, D(103))
    assert (through_target.gap_through, through_target.r_multiple) == (True, D("3.0000"))


def test_neither_barrier_is_a_timeout_and_never_counts_as_a_loss():
    found = outcome([candle(index, 100, "100.4", "99.6", 100) for index in range(1, 6)])
    assert found.result is BarrierResult.TIMEOUT
    assert found.target_first is None  # not a win and not a loss: an unresolved barrier
    assert (found.candles_to_outcome, found.complete) == (None, True)
    assert found.r_multiple == D("0.0000")  # marked at the last close, reported separately from wins/losses


def test_missing_bars_give_no_data_rather_than_a_result():
    found = outcome([])
    assert (found.result, found.r_multiple, found.complete) == (BarrierResult.NO_DATA, None, False)
    assert found.target_first is None


def test_a_window_shorter_than_the_horizon_is_incomplete_rather_than_padded():
    found = outcome([candle(1, 100, "100.4", "99.6", 100)], max_candles=5)
    assert (found.result, found.candles_used, found.complete) == (BarrierResult.TIMEOUT, 1, False)


def test_the_first_barrier_touched_wins_even_if_a_later_candle_would_reverse_it():
    future = [candle(1, 100, "100.5", "98.5", 99), candle(2, 99, 105, 98, 104)]
    assert outcome(future).result is BarrierResult.STOP  # the walk stops at the first resolution


def test_excursions_are_measured_in_r_from_the_entry():
    found = outcome([candle(1, 100, "101.5", "99.5", 101), candle(2, 101, "102.5", "99.2", 102)])
    assert found.result is BarrierResult.TARGET
    # The excursion is the extreme actually printed (102.5), not the barrier price the exit was booked at.
    assert found.mfe_r == D("2.5000")
    assert found.mae_r == D("-0.8000")  # 99.2 against a one-point risk


def test_a_stop_candles_favourable_extreme_never_enters_the_mfe():
    """Spec v1.2 D3, the platform's own rule, applied to research labels so the two stay comparable."""
    found = outcome([candle(1, 100, "101.5", "98.5", 99)])
    assert found.result is BarrierResult.STOP
    assert found.mfe_r == D("0.0000")  # the 101.5 high is discarded: the stop may have come first
    assert found.mae_r == D("-1.5000")  # the adverse extreme still counts
    # A short is the mirror: the favourable low of its stop candle is discarded too.
    short = outcome([candle(1, 100, "101.5", "98.5", 101)], direction=Direction.SHORT, entry=D(100),
                    stop=D(101), target=D(98))
    assert (short.result, short.mfe_r) == (BarrierResult.STOP, D("0.0000"))


def test_a_short_is_the_exact_mirror():
    short = outcome([candle(1, 100, "100.5", "97.5", 98)], direction=Direction.SHORT, entry=D(100),
                    stop=D(101), target=D(98))
    assert (short.result, short.exit_price, short.r_multiple) == (BarrierResult.TARGET, D(98), D("2.0000"))
    stopped = outcome([candle(1, 100, "101.5", "99.5", 101)], direction=Direction.SHORT, entry=D(100),
                      stop=D(101), target=D(98))
    assert (stopped.result, stopped.r_multiple) == (BarrierResult.STOP, D("-1.0000"))


def test_barriers_must_be_on_the_correct_side_of_the_entry():
    with pytest.raises(ValueError, match="long needs stop < entry < target"):
        outcome([candle(1, 100, 101, 99, 100)], stop=D(101))
    with pytest.raises(ValueError, match="short needs target < entry < stop"):
        outcome([candle(1, 100, 101, 99, 100)], direction=Direction.SHORT, stop=D(99), target=D(101))
    with pytest.raises(ValueError, match="max_candles must be >= 1"):
        outcome([candle(1, 100, 101, 99, 100)], max_candles=0)


def test_atr_barriers_are_multiples_of_the_entry_on_the_right_side():
    stop, target = atr_barriers(D(100), D(2), Direction.LONG)
    assert (stop, target) == (D(98), D(104))
    short_stop, short_target = atr_barriers(D(100), D(2), Direction.SHORT)
    assert (short_stop, short_target) == (D(102), D(96))
    with pytest.raises(ValueError, match="atr must be positive"):
        atr_barriers(D(100), D(0), Direction.LONG)


# --- fixed horizon -----------------------------------------------------------------------------------------
def test_a_fixed_horizon_reports_return_mfe_and_mae():
    future = [candle(1, 100, 103, 99, 102), candle(2, 102, 104, 98, 101), candle(3, 101, 102, 100, "101.5")]
    found = fixed_horizon(future, entry=D(100), direction=Direction.LONG, horizon=3)
    assert (found.return_pct, found.mfe_pct, found.mae_pct) == (D("1.5000"), D("4.0000"), D("-2.0000"))
    assert (found.candles_used, found.complete, found.exit_price) == (3, True, D("101.5"))


def test_a_short_horizon_measures_the_move_from_the_trade_side():
    future = [candle(1, 100, 101, 96, 97)]
    found = fixed_horizon(future, entry=D(100), direction=Direction.SHORT, horizon=1)
    assert (found.return_pct, found.mfe_pct, found.mae_pct) == (D("3.0000"), D("4.0000"), D("-1.0000"))


def test_a_horizon_beyond_the_series_is_incomplete_and_an_empty_one_has_no_return():
    future = [candle(1, 100, 101, 99, 100)]
    partial = fixed_horizon(future, entry=D(100), direction=Direction.LONG, horizon=5)
    assert (partial.candles_used, partial.complete) == (1, False)
    empty = fixed_horizon([], entry=D(100), direction=Direction.LONG, horizon=5)
    assert (empty.complete, empty.return_pct, empty.exit_price) == (False, None, None)
    with pytest.raises(ValueError, match="horizon must be >= 1"):
        fixed_horizon(future, entry=D(100), direction=Direction.LONG, horizon=0)
