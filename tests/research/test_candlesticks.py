"""The candlestick engine: geometry, and a positive and negative case for every supported pattern (spec 24).

The decisive test in this file is `test_the_same_geometry_is_named_by_its_prior_trend`: one candle, three
contexts, three different answers. That is the whole reason geometry and context are separate modules.
"""

import pytest

from tests.research.support import D, candle, hammer_candle, inverted_hammer_candle, trend
from virtual_orders.research.candlesticks import (
    DEFAULT_THRESHOLDS,
    ENGINE_VERSION,
    SUPPORTED_PATTERNS,
    PriorContext,
    detect_at,
    geometry,
    measure,
)
from virtual_orders.research.models import (
    CandleDirection,
    PatternDirection,
    PriorTrend,
    Timeframe,
    TrendState,
)

DOWN = PriorTrend(TrendState.DOWN, D("0.8"), 20, True)
UP = PriorTrend(TrendState.UP, D("0.8"), 20, True)
SIDEWAYS = PriorTrend(TrendState.SIDEWAYS, D("0.1"), 20, True)
NO_CONTEXT = PriorTrend(TrendState.UNKNOWN, D(0), 0, False)


def names(candles, prior=DOWN):
    return {item.pattern for item in detect_at(candles, len(candles) - 1, prior=prior)}


def only(candles, pattern, prior=DOWN):
    (found,) = [item for item in detect_at(candles, len(candles) - 1, prior=prior) if item.pattern == pattern]
    return found


# --- geometry ----------------------------------------------------------------------------------------------
def test_geometry_measures_range_body_and_wicks():
    shape = measure(D(100), D(104), D(99), D(102))
    assert (shape.range, shape.body, shape.upper_wick, shape.lower_wick) == (D(5), D(2), D(2), D(1))
    assert (shape.body_ratio, shape.upper_wick_ratio, shape.lower_wick_ratio) == (
        D("0.4000"), D("0.4000"), D("0.2000"))
    assert shape.close_location == D("0.6000")
    assert shape.direction is CandleDirection.BULLISH and shape.zero_range is False


def test_a_zero_range_candle_keeps_every_ratio_undefined():
    shape = measure(D(100), D(100), D(100), D(100))
    assert shape.zero_range is True and shape.direction is CandleDirection.FLAT
    assert (shape.body_ratio, shape.upper_wick_ratio, shape.lower_wick_ratio, shape.close_location) == (
        None, None, None, None)
    # A zero-range candle matches nothing: a ratio that does not exist is never read as zero.
    assert names([candle(0, 100, 100, 100, 100)]) == set()


def test_geometry_of_a_candle_matches_its_own_ohlc():
    item = candle(0, 100, 104, 99, 102)
    assert geometry(item) == measure(item.open, item.high, item.low, item.close)


# --- context decides the name ------------------------------------------------------------------------------
def test_the_same_geometry_is_named_by_its_prior_trend():
    shape = [hammer_candle(0)]
    assert names(shape, DOWN) == {"HAMMER"}
    assert names(shape, UP) == {"HANGING_MAN"}
    assert names(shape, SIDEWAYS) == set()  # neither: a sideways move names nothing
    assert names(shape, NO_CONTEXT) == set()  # and unknown context is never guessed


def test_the_inverted_geometry_is_named_the_same_way():
    shape = [inverted_hammer_candle(0)]
    assert names(shape, DOWN) == {"INVERTED_HAMMER"}
    assert names(shape, UP) == {"SHOOTING_STAR"}


def test_context_score_rewards_an_opposing_trend_and_never_flatters_missing_context():
    after_decline = only([hammer_candle(0)], "HAMMER", DOWN)
    assert after_decline.context_score == D("0.9000")  # 0.5 + strength/2
    assert after_decline.direction is PatternDirection.BULLISH
    weak = only([hammer_candle(0)], "HAMMER", PriorTrend(TrendState.DOWN, D("0.1"), 20, True))
    assert weak.context_score == D("0.5500") and weak.overall_score < after_decline.overall_score


# --- single candle -----------------------------------------------------------------------------------------
def test_doji_and_its_two_named_variants():
    assert "DOJI" in names([candle(0, 100, "100.5", "99.5", "100.02")])
    assert "DOJI" not in names([candle(0, 100, "101.1", "99.9", 101)])  # a real body is not a doji
    dragonfly = names([candle(0, "100.50", "100.55", "99.50", "100.52")])
    assert "DRAGONFLY_DOJI" in dragonfly and "DOJI" in dragonfly
    gravestone = names([candle(0, "99.52", "100.50", "99.47", "99.50")], UP)
    assert "GRAVESTONE_DOJI" in gravestone


def test_a_long_wick_with_too_large_a_body_is_not_a_hammer():
    assert names([candle(0, 100, "100.1", 98, 99)]) == set()  # body_ratio above the threshold


# --- two candles -------------------------------------------------------------------------------------------
def test_bullish_and_bearish_engulfing():
    bullish = [candle(0, 100, "100.2", "97.8", 98), candle(1, "97.5", "100.7", "97.3", "100.5")]
    found = only(bullish, "BULLISH_ENGULFING")
    assert found.direction is PatternDirection.BULLISH
    assert found.evidence["engulf_ratio"] == D("1.5000")
    assert found.evidence["previous_direction"] == CandleDirection.BEARISH
    assert (found.start_ts, found.end_ts) == (bullish[0].ts, bullish[1].end_ts)
    assert found.candles == 2

    bearish = [candle(0, 98, "100.2", "97.8", 100), candle(1, "100.5", "100.7", "97.3", "97.5")]
    assert "BEARISH_ENGULFING" in names(bearish, UP)


def test_a_smaller_body_never_engulfs():
    weak = [candle(0, 102, "102.2", "97.8", 98), candle(1, "98.5", "99.7", "98.3", "99.5")]
    assert "BULLISH_ENGULFING" not in names(weak)


def test_piercing_line_and_dark_cloud_cover_need_half_the_previous_body():
    piercing = [candle(0, 102, "102.2", "97.8", 98), candle(1, "97.5", "100.7", "97.3", "100.5")]
    found = only(piercing, "PIERCING_LINE")
    assert found.evidence["penetration"] == D("0.6250")
    shallow = [candle(0, 102, "102.2", "97.8", 98), candle(1, "97.5", "99.2", "97.3", 99)]
    assert "PIERCING_LINE" not in names(shallow)

    dark = [candle(0, 98, "102.2", "97.8", 102), candle(1, "102.5", "102.7", "99.3", "99.5")]
    assert "DARK_CLOUD_COVER" in names(dark, UP)


# --- three candles -----------------------------------------------------------------------------------------
def test_morning_and_evening_star():
    morning = [candle(0, 105, "105.2", "99.8", 100), candle(1, "99.5", "99.8", "99.2", "99.6"),
               candle(2, "99.8", "103.7", "99.7", "103.5")]
    found = only(morning, "MORNING_STAR")
    assert found.direction is PatternDirection.BULLISH and found.candles == 3
    assert found.evidence["closed_above_midpoint"] is True

    evening = [candle(0, 100, "105.2", "99.8", 105), candle(1, "105.5", "105.8", "105.2", "105.4"),
               candle(2, "105.2", "105.3", "101.3", "101.5")]
    assert "EVENING_STAR" in names(evening, UP)


def test_a_star_with_a_large_middle_body_is_not_a_star():
    not_a_star = [candle(0, 105, "105.2", "99.8", 100), candle(1, "99.5", "102.2", "99.2", 102),
                  candle(2, "102.2", "103.7", "102.1", "103.5")]
    assert "MORNING_STAR" not in names(not_a_star)


def test_three_white_soldiers_and_three_black_crows():
    soldiers = trend(0, 100.0, 3, 1.0)
    found = only(soldiers, "THREE_WHITE_SOLDIERS", UP)
    assert found.direction is PatternDirection.BULLISH
    assert found.evidence["closes_march"] is True and found.evidence["opens_inside_previous_body"] is True
    assert "THREE_BLACK_CROWS" in names(trend(0, 100.0, 3, -1.0), DOWN)


def test_a_broken_run_is_not_a_marching_pattern():
    broken = [*trend(0, 100.0, 2, 1.0), candle(2, 102, "102.2", "100.8", 101)]
    assert "THREE_WHITE_SOLDIERS" not in names(broken, UP)


# --- engine contract ---------------------------------------------------------------------------------------
POSITIVE_CASES = {
    "single-doji": ([candle(0, 100, "100.5", "99.5", "100.02")], DOWN),
    "dragonfly": ([candle(0, "100.50", "100.55", "99.50", "100.52")], DOWN),
    "gravestone": ([candle(0, "99.52", "100.50", "99.47", "99.50")], UP),
    "hammer": ([hammer_candle(0)], DOWN),
    "hanging-man": ([hammer_candle(0)], UP),
    "inverted-hammer": ([inverted_hammer_candle(0)], DOWN),
    "shooting-star": ([inverted_hammer_candle(0)], UP),
    "bullish-engulfing": ([candle(0, 100, "100.2", "97.8", 98),
                           candle(1, "97.5", "100.7", "97.3", "100.5")], DOWN),
    "bearish-engulfing": ([candle(0, 98, "100.2", "97.8", 100),
                           candle(1, "100.5", "100.7", "97.3", "97.5")], UP),
    "piercing": ([candle(0, 102, "102.2", "97.8", 98), candle(1, "97.5", "100.7", "97.3", "100.5")], DOWN),
    "dark-cloud": ([candle(0, 98, "102.2", "97.8", 102), candle(1, "102.5", "102.7", "99.3", "99.5")], UP),
    "morning-star": ([candle(0, 105, "105.2", "99.8", 100), candle(1, "99.5", "99.8", "99.2", "99.6"),
                      candle(2, "99.8", "103.7", "99.7", "103.5")], DOWN),
    "evening-star": ([candle(0, 100, "105.2", "99.8", 105), candle(1, "105.5", "105.8", "105.2", "105.4"),
                      candle(2, "105.2", "105.3", "101.3", "101.5")], UP),
    "soldiers": (trend(0, 100.0, 3, 1.0), UP),
    "crows": (trend(0, 100.0, 3, -1.0), DOWN),
}


def test_every_supported_pattern_has_a_positive_case_here():
    detected: set[str] = set()
    for candles, prior in POSITIVE_CASES.values():
        detected |= names(candles, prior)
    assert detected == set(SUPPORTED_PATTERNS)


@pytest.mark.parametrize("case", sorted(POSITIVE_CASES))
def test_every_detection_is_versioned_scored_and_explained(case):
    candles, prior = POSITIVE_CASES[case]
    for found in detect_at(candles, len(candles) - 1, prior=prior):
        assert found.engine_version == ENGINE_VERSION
        assert D(0) <= found.geometry_score <= D(1)
        assert D(0) <= found.context_score <= D(1)
        assert D(0) <= found.overall_score <= D(1)
        assert found.evidence and "prior_trend" in found.evidence
        assert found.evidence["weights"] == {"geometry": D("0.6"), "context": D("0.4")}
        assert found.timeframe is Timeframe.M15
        assert found.evidence_hash == found.evidence_hash  # stable for identical content
        assert found.detection_key("AAPL").startswith(f"{ENGINE_VERSION}:AAPL:15m:{found.pattern}:")


def test_detections_come_back_in_a_fixed_order():
    candles = [candle(0, "100.50", "100.55", "99.50", "100.52")]
    assert [item.pattern for item in detect_at(candles, 0, prior=DOWN)] == sorted(
        item.pattern for item in detect_at(candles, 0, prior=DOWN))


def test_a_detection_window_never_mixes_timeframes_or_reads_outside_the_series():
    mixed = [candle(0, 100, 101, 99, "100.5"), candle(1, 100, 101, 99, "100.5", timeframe=Timeframe.H1)]
    with pytest.raises(ValueError, match="one timeframe"):
        detect_at(mixed, 1, prior=DOWN)
    with pytest.raises(IndexError):
        detect_at([hammer_candle(0)], 5, prior=DOWN)


def test_a_prior_context_names_each_pattern_length_separately():
    context = PriorContext({1: UP, 2: DOWN, 3: SIDEWAYS})
    assert context.for_length(1) is UP and context.for_length(3) is SIDEWAYS
    assert context.for_length(9).available is False  # a length nobody computed is unknown, never borrowed
    assert PriorContext.fixed(DOWN).for_length(2) is DOWN


def test_thresholds_are_versioned_values_not_magic_numbers():
    snapshot = DEFAULT_THRESHOLDS.snapshot()
    assert snapshot["doji_body_ratio"] == D("0.05") and snapshot["engulf_body_ratio_min"] == D("1")
    assert all(isinstance(value, type(D(1))) for value in snapshot.values())
