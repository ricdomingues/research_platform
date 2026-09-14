from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.domain.models import Bar
from virtual_orders.analytics.pressure import (
    DISCLAIMER,
    METHOD,
    PressureSide,
    close_location_value,
    estimate_pressure,
    strong_pressure,
)


def bar(minute: int, o, h, l, c, v) -> Bar:  # noqa: E741
    return Bar(ts=datetime(2025, 11, 25, 14, 30 + minute, tzinfo=UTC), open=Decimal(str(o)), high=Decimal(str(h)),
               low=Decimal(str(l)), close=Decimal(str(c)), volume=Decimal(str(v)))


BUYING = [bar(0, 10, 11, 9, 10.5, 100), bar(1, 10.5, 12, 10, 12, 200), bar(2, 12, 12.5, 11.5, 12, 100)]
SELLING = [bar(0, 10, 11, 9, 9.5, 100), bar(1, 9.5, 10, 8, 8, 200), bar(2, 8, 8.5, 7.5, 8, 100)]


def test_close_location_value():
    assert close_location_value(BUYING[0]) == Decimal("0.5")
    assert close_location_value(BUYING[1]) == Decimal("1")
    assert close_location_value(bar(0, 5, 5, 5, 5, 10)) == Decimal("0")  # zero range


def test_buying_fixture_matches_hand_computed_values():
    # CLV = 0.5, 1, 0 -> money flow 50 + 200 + 0 = 250 over volume 400 -> CMF 0.625
    # OBV = +200 (close up), 0 (unchanged) -> 200 / 2 bars / mean volume 133.33 -> 0.75
    # VWAP uses the typical price (H+L+C)/3 of each bar (D27, D37):
    #   bar 0: (11 + 9 + 10.5)/3 = 30.5/3 = 10.166666...  x 100 = 1016.666666...
    #   bar 1: (12 + 10 + 12)/3  = 34/3   = 11.333333...  x 200 = 2266.666666...
    #   bar 2: (12.5 + 11.5 + 12)/3 = 36/3 = 12           x 100 = 1200
    #   VWAP = 4483.333333... / 400 = 11.208333...  -> 11.2083 (ROUND_HALF_EVEN, 4 dp)
    # distance = (12 - 11.208333...) / 11.208333... * 100 = 0.791666.../11.208333... * 100 = 7.063197...  -> 7.0632
    estimate = estimate_pressure(BUYING)
    assert estimate is not None
    assert estimate.method == METHOD and estimate.bars == 3
    assert estimate.first_bar_ts == BUYING[0].ts and estimate.last_bar_ts == BUYING[2].ts
    assert estimate.close_location_value == Decimal("0.0000")
    assert estimate.chaikin_money_flow == Decimal("0.6250")
    assert estimate.obv_slope == Decimal("0.7500")
    assert estimate.vwap == Decimal("11.2083")
    assert estimate.vwap_distance_pct == Decimal("7.0632")
    assert strong_pressure(estimate, Decimal("0.625")) is PressureSide.BUY
    assert strong_pressure(estimate, Decimal("0.6251")) is None


def test_selling_fixture_is_the_mirror():
    estimate = estimate_pressure(SELLING)
    assert estimate is not None
    assert estimate.chaikin_money_flow == Decimal("-0.6250") and estimate.obv_slope == Decimal("-0.7500")
    assert estimate.vwap_distance_pct < 0
    assert strong_pressure(estimate, Decimal("0.5")) is PressureSide.SELL


def test_order_of_input_does_not_matter_and_results_are_deterministic():
    assert estimate_pressure(list(reversed(BUYING))) == estimate_pressure(BUYING) == estimate_pressure(BUYING)


def test_zero_volume_has_neutral_flow_and_vwap_at_the_last_close():
    estimate = estimate_pressure([bar(0, 10, 11, 9, 10.5, 0), bar(1, 10.5, 12, 10, 12, 0)])
    assert estimate is not None
    assert estimate.chaikin_money_flow == 0 and estimate.obv_slope == 0
    assert estimate.vwap == Decimal("12.0000") and estimate.vwap_distance_pct == 0
    assert strong_pressure(estimate, Decimal("0.1")) is None


def test_too_few_bars_or_duplicate_minutes():
    assert estimate_pressure([]) is None and estimate_pressure(BUYING[:1]) is None
    with pytest.raises(ValueError, match="duplicate bar minute"):
        estimate_pressure([BUYING[0], BUYING[0]])


def test_disclaimer_labels_the_output_as_an_estimate():
    assert "Estimate" in DISCLAIMER and "not order-flow" in DISCLAIMER
