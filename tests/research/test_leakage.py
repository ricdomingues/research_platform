"""No look-ahead (spec 24): rewriting the future must not change anything measured in the past.

This is the file that would catch the mistake that makes candlestick research worthless — a feature computed
at a candle that quietly depends on candles after it. Rather than inspecting the code, each test rewrites
every later candle to an absurd value and asserts that the earlier measurement is bit-identical.
"""

from decimal import Decimal

from tests.research.support import BASE, candle, zigzag
from tests.support import bar, et
from virtual_orders.analytics.pressure import estimate_pressure
from virtual_orders.research.backtest import build_occurrences, prior_context
from virtual_orders.research.candlesticks import detect_at
from virtual_orders.research.features import build_snapshot
from virtual_orders.research.indicators import compute_series
from virtual_orders.research.market_structure import (
    SwingKind,
    confirmed_swings,
    prior_trend,
    structure_at,
    swing_points,
)

CUT = 40


def rewritten(series, *, from_index=CUT + 1, price=500):
    """The same history, then a completely different future."""
    return series[:from_index] + [candle(index, price, price + 5, price - 5, price)
                                  for index in range(from_index, len(series))]


def peaked():
    """A series with one unambiguous peak, so a strict pivot actually forms at its highest candle.

    The wick of the peak candle is deliberately taller than its neighbours': pivots are strict, so a plateau of
    equal highs would produce no pivot at all.
    """
    closes = [100, 101, 102, 103, 102, 101, 100]
    series, price = [], 99.8
    for index, close in enumerate(closes):
        high = max(price, close) + (1.0 if index == 3 else 0.2)
        series.append(candle(index, price, high, min(price, close) - 0.2, close))
        price = close
    return series


def test_indicators_at_a_candle_ignore_every_later_candle():
    series = zigzag(cycles=3)
    original, altered = compute_series(series), compute_series(rewritten(series))
    for index in range(CUT + 1):
        assert original.ema_at(20, index) == altered.ema_at(20, index)
        assert original.rsi_at(index) == altered.rsi_at(index)
        assert original.atr_at(index) == altered.atr_at(index)
        assert original.relative_volume_at(index) == altered.relative_volume_at(index)
        assert original.volatility_at(index) == altered.volatility_at(index)
        assert original.macd_at(index) == altered.macd_at(index)


def test_market_structure_at_a_candle_ignores_every_later_candle():
    series = zigzag(cycles=3)
    altered = rewritten(series)
    at_original = structure_at(series, CUT, compute_series(series), swings=swing_points(series))
    at_altered = structure_at(altered, CUT, compute_series(altered), swings=swing_points(altered))
    assert at_original == at_altered


def test_a_swing_pivot_is_invisible_until_the_candles_that_confirm_it_have_closed():
    series = peaked()
    (pivot,) = [item for item in swing_points(series) if item.kind is SwingKind.HIGH]
    assert (pivot.index, pivot.confirmed_index) == (3, 5)  # confirmed two candles after the pivot itself
    assert [item.index for item in confirmed_swings(swing_points(series), 4)] == []
    assert [item.index for item in confirmed_swings(swing_points(series), 5)] == [3]

    indicators = compute_series(series)
    # One candle before confirmation the level does not exist yet; one candle later it does. A human drawing
    # the line on a finished chart would have "seen" it at index 3, which is exactly the leak this prevents.
    before = structure_at(series, 4, indicators, swings=swing_points(series))
    after = structure_at(series, 6, indicators, swings=swing_points(series))
    assert before.resistance is None and before.swings_confirmed == 0
    assert after.resistance == pivot.price and after.swings_confirmed == 1


def test_the_prior_trend_of_a_pattern_never_contains_the_pattern_itself():
    series = zigzag(cycles=3)
    context = prior_context(series, CUT)
    assert context.for_length(1) == prior_trend(series, CUT)
    assert context.for_length(2) == prior_trend(series, CUT - 1)
    assert context.for_length(3) == prior_trend(series, CUT - 2)
    # The prior is read from earlier candles only, so rewriting the future leaves it untouched.
    assert prior_context(rewritten(series), CUT).by_length == context.by_length


def test_a_detection_and_its_feature_snapshot_are_unchanged_by_the_future():
    series = zigzag(cycles=3)
    altered = rewritten(series)
    found = detect_at(series[: CUT + 1], CUT, prior=prior_context(series, CUT))
    found_altered = detect_at(altered[: CUT + 1], CUT, prior=prior_context(altered, CUT))
    assert [item.evidence_hash for item in found] == [item.evidence_hash for item in found_altered]

    def snapshot(candles, detection):
        indicators = compute_series(candles)
        return build_snapshot(
            list(candles[: CUT + 1]), CUT, ticker="AAPL", data_as_of=BASE, series=indicators,
            structure=structure_at(candles[: CUT + 1], CUT, indicators, swings=swing_points(candles)),
            detection=detection,
        )

    for detection, altered_detection in zip(found, found_altered, strict=True):
        assert snapshot(series, detection).feature_hash == snapshot(altered, altered_detection).feature_hash


def test_the_whole_occurrence_pipeline_keeps_features_fixed_while_labels_move():
    series = zigzag(cycles=4)
    altered = rewritten(series, from_index=CUT + 1, price=900)
    original = {item.signal_end_ts: item for item in build_occurrences(series, ticker="AAPL", data_as_of=BASE)}
    changed = {item.signal_end_ts: item for item in build_occurrences(altered, ticker="AAPL", data_as_of=BASE)}
    shared = sorted(set(original) & set(changed))
    early = [ts for ts in shared if ts <= series[CUT].end_ts]
    assert early, "the fixture must produce occurrences before the rewrite point"
    for ts in early:
        assert original[ts].features.feature_hash == changed[ts].features.feature_hash
        assert original[ts].detection.evidence_hash == changed[ts].detection.evidence_hash

    # The labels are the half that must move: an occurrence whose label window reaches past the cut read
    # candles that no longer exist in the rewritten series.
    crossing = [ts for ts in early
                if original[ts].label_end_ts is not None and original[ts].label_end_ts > series[CUT].end_ts]
    assert crossing, "the fixture must have at least one label window crossing the rewrite point"
    assert any(original[ts].barrier != changed[ts].barrier for ts in crossing)


def test_a_feature_snapshot_never_sees_a_pressure_estimate_from_after_its_candle():
    """`build_snapshot` records whatever pressure it is handed, so the caller cuts the window at the candle.

    The scan and the backtester both do that; this pins the contract that the snapshot carries the estimate
    verbatim, which is why the cut has to happen before it arrives here.
    """
    minutes = [et("2025-11-25", f"09:{index:02d}") for index in range(30, 60)]
    bars = [bar(minute, 100, 101, 99, Decimal("100.5")) for minute in minutes]
    estimate = estimate_pressure(bars)
    assert estimate is not None and estimate.last_bar_ts == minutes[-1]
    series = zigzag(cycles=2)
    indicators = compute_series(series)
    snapshot = build_snapshot(
        list(series[: CUT + 1]), CUT, ticker="AAPL", data_as_of=BASE, series=indicators,
        structure=structure_at(series[: CUT + 1], CUT, indicators, swings=swing_points(series)),
        pressure=estimate,
    )
    assert snapshot.cmf == estimate.chaikin_money_flow
    assert snapshot.vwap == estimate.vwap
    assert snapshot.pressure_estimate is True  # always labelled as an estimate, never as order flow
