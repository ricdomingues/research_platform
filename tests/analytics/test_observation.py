from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from virtual_orders.analytics.observation import (
    ASSOCIATION_NOTE,
    MIN_TRADES_FOR_BUCKET_MEAN,
    UNKNOWN_STATE,
    Alignment,
    LatencyStats,
    PressureBucket,
    StateChange,
    Strength,
    TradeStats,
    classify_pressure,
    coverage_pct,
    failure_code,
    latency_stats,
    nearest_rank,
    pressure_buckets,
    ratio,
    seconds_by_state,
    trade_stats,
    whole_seconds,
)
from virtual_orders.analytics.pressure import METHOD, PressureEstimate

T0 = datetime(2025, 11, 25, 5, 0, tzinfo=UTC)


def estimate(cmf: str, obv: str = "1", distance: str = "0.5") -> PressureEstimate:
    return PressureEstimate(
        method=METHOD, bars=30, first_bar_ts=T0, last_bar_ts=T0 + timedelta(minutes=29),
        close_location_value=Decimal("0"), chaikin_money_flow=Decimal(cmf), obv_slope=Decimal(obv),
        vwap=Decimal("100"), vwap_distance_pct=Decimal(distance),
    )


def test_whole_seconds_floors_and_rejects_naive_datetimes():
    assert whole_seconds(T0, T0 + timedelta(minutes=65, microseconds=999_999)) == 3900
    assert whole_seconds(T0, T0 - timedelta(microseconds=1)) == -1
    with pytest.raises(ValueError, match="naive"):
        whole_seconds(datetime(2025, 11, 25), T0)  # noqa: DTZ001


def test_nearest_rank_percentiles_never_interpolate():
    assert nearest_rank([], 50) is None
    assert nearest_rank([30], 90) == 30
    assert nearest_rank([40, 10, 30, 20], 50) == 20  # ceil(0.5 * 4) = 2nd smallest
    assert nearest_rank(list(range(1, 11)), 90) == 9
    assert nearest_rank(list(range(1, 11)), 100) == 10
    with pytest.raises(ValueError):
        nearest_rank([1], 0)
    assert latency_stats([3900, 60, 600]) == LatencyStats(count=3, median_seconds=600, p90_seconds=3900,
                                                          max_seconds=3900)
    assert latency_stats([]) == LatencyStats(0, None, None, None)


def test_ratios_and_coverage_are_quantized_decimals():
    assert ratio(1, 3) == Decimal("0.3333") and ratio(0, 0) is None
    assert coverage_pct(390, 35) == Decimal("91.03") and coverage_pct(0, 0) is None


def test_trade_stats_follow_the_r_sign_and_median_rules():
    stats = trade_stats([
        (Decimal("1.75"), Decimal("2.375"), Decimal("-0.1")),
        (Decimal("-1"), Decimal("0.5"), Decimal("-1.2")),
        (Decimal("0"), None, None),
        (Decimal("0.5"), Decimal("1"), Decimal("-0.3")),
    ])
    assert stats == TradeStats(
        trades=4, wins=2, losses=1, win_rate=Decimal("0.5000"), sum_r=Decimal("1.2500"), mean_r=Decimal("0.3125"),
        mean_mfe_r=Decimal("1.2917"), median_mfe_r=Decimal("1.0000"), mean_mae_r=Decimal("-0.5333"),
        median_mae_r=Decimal("-0.3000"),
    )
    empty = trade_stats([])
    assert (empty.trades, empty.win_rate, empty.sum_r, empty.mean_r, empty.median_mfe_r) == (
        0, None, Decimal("0.0000"), None, None)
    assert str(trade_stats([(Decimal("0"), None, None)]).sum_r) == "0.0000"  # never "-0.0000"


def test_seconds_by_state_splits_the_window_at_each_change():
    start, end = T0, T0 + timedelta(hours=24)
    changes = [StateChange(T0 + timedelta(hours=10), "DEGRADED"), StateChange(T0 + timedelta(hours=10, minutes=30),
                                                                               "DEGRADED"),
               StateChange(T0 + timedelta(hours=11), "HEALTHY")]
    assert seconds_by_state("HEALTHY", changes, start, end) == {"DEGRADED": 3600, "HEALTHY": 82800}
    assert seconds_by_state(None, [StateChange(T0, "HEALTHY")], start, end) == {"HEALTHY": 86400}
    assert seconds_by_state(None, [], start, end) == {UNKNOWN_STATE: 86400}
    assert seconds_by_state("HEALTHY", [], end, start) == {}
    with pytest.raises(ValueError, match="outside"):
        seconds_by_state("HEALTHY", [StateChange(end, "DEGRADED")], start, end)


@pytest.mark.parametrize("value, direction, expected", [
    (estimate("0.2"), "LONG", (Alignment.ALIGNED, Strength.STRONG)),
    (estimate("0.2"), "SHORT", (Alignment.OPPOSED, Strength.STRONG)),
    (estimate("-0.08", obv="-1", distance="-0.2"), "SHORT", (Alignment.ALIGNED, Strength.MODERATE)),
    (estimate("0.08", obv="-1"), "LONG", (Alignment.NEUTRAL, Strength.MODERATE)),  # OBV falling: no strong side
    (estimate("0.0137"), "LONG", (Alignment.NEUTRAL, Strength.WEAK)),
    (None, "LONG", (Alignment.UNAVAILABLE, None)),
])
def test_pressure_is_classified_relative_to_the_trade_direction(value, direction, expected):
    assert classify_pressure(value, direction) == expected


def test_an_unknown_direction_is_rejected():
    with pytest.raises(ValueError, match="direction"):
        classify_pressure(estimate("0.2"), "FLAT")


def test_buckets_group_results_in_a_fixed_order_and_need_five_trades_for_a_mean():
    buckets = pressure_buckets([
        (Alignment.UNAVAILABLE, None, Decimal("-1")),
        (Alignment.ALIGNED, Strength.STRONG, Decimal("1.75")),
        (Alignment.ALIGNED, Strength.WEAK, Decimal("0.5")),
        (Alignment.ALIGNED, Strength.STRONG, Decimal("-1")),
        *((Alignment.NEUTRAL, Strength.WEAK, Decimal(r)) for r in ("1", "-1", "0.5", "0.5", "1")),
    ])
    assert buckets == [
        PressureBucket(Alignment.ALIGNED, Strength.WEAK, 1, 1, Decimal("0.5000"), None),
        PressureBucket(Alignment.ALIGNED, Strength.STRONG, 2, 1, Decimal("0.7500"), None),
        PressureBucket(Alignment.NEUTRAL, Strength.WEAK, 5, 4, Decimal("2.0000"), Decimal("0.4000")),
        PressureBucket(Alignment.UNAVAILABLE, None, 1, 0, Decimal("-1.0000"), None),
    ]
    assert MIN_TRADES_FOR_BUCKET_MEAN == 5
    assert "not causal" in ASSOCIATION_NOTE and "never used by any evaluation" in ASSOCIATION_NOTE
    assert "at least 5 trades" in ASSOCIATION_NOTE


@pytest.mark.parametrize("message, code", [
    ("UNKNOWN_DATA_SOURCE: no bar source registered for nope", "UNKNOWN_DATA_SOURCE"),
    ("ERROR:KeyError", "UNEXPECTED_ERROR"),
    ("HTTP 429 from https://data.alpaca.markets?key=secret", "SOURCE_ERROR"),
    ("SourceUnavailable", "SOURCE_ERROR"),
])
def test_failure_messages_become_fixed_codes(message, code):
    assert failure_code(message) == code
