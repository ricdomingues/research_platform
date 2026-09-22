"""Content identity of the bars behind a reading (Plan 6, D96)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from core.domain.models import Bar
from virtual_orders.research.identity import (
    bars_content_hash,
    candle_input_bars,
    candle_input_hash,
    candles_input_hash,
)
from virtual_orders.research.models import Candle, Timeframe


def bar(minute: int, close: str = "10.00", batch_id=None) -> Bar:
    ts = datetime(2026, 9, 21, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
    return Bar(ts=ts, open=Decimal("10.00"), high=Decimal("10.50"), low=Decimal("9.50"),
               close=Decimal(close), volume=Decimal("1000"), batch_id=batch_id or uuid4())


def candle(minutes: int = 15) -> Candle:
    start = datetime(2026, 9, 21, 13, 30, tzinfo=UTC)
    return Candle(
        timeframe=Timeframe.M15, ts=start, end_ts=start + timedelta(minutes=minutes - 1),
        session_day=start.date(), open=Decimal("10.00"), high=Decimal("10.50"), low=Decimal("9.50"),
        close=Decimal("10.00"), volume=Decimal("15000"), minutes_expected=minutes,
        minutes_present=minutes, truncated=False,
    )


def test_the_same_data_delivered_by_a_different_batch_has_the_same_identity():
    """A re-ingestion that changes nothing is a non-event: same content, same hash, no supersession."""
    first = [bar(index) for index in range(15)]
    second = [bar(index) for index in range(15)]  # fresh batch ids, identical values
    assert [item.batch_id for item in first] != [item.batch_id for item in second]
    assert bars_content_hash(first) == bars_content_hash(second)


def test_a_corrected_close_changes_the_identity():
    original = [bar(index) for index in range(15)]
    corrected = [bar(index) for index in range(14)] + [bar(14, close="10.25")]
    assert bars_content_hash(original) != bars_content_hash(corrected)


def test_a_missing_bar_changes_the_identity():
    """Absence is data: a candle built from 14 minutes is not the candle built from 15."""
    full = [bar(index) for index in range(15)]
    assert bars_content_hash(full) != bars_content_hash(full[:-1])


def test_only_the_bars_inside_the_candle_count():
    series = [bar(index) for index in range(30)]
    inside = candle_input_bars(series, candle())
    assert [item.ts for item in inside] == [item.ts for item in series[:15]]
    assert candle_input_hash(series, candle()) == bars_content_hash(series[:15])


def test_order_is_normalised_so_read_order_cannot_change_identity():
    series = [bar(index) for index in range(15)]
    assert bars_content_hash(list(reversed(series))) == bars_content_hash(series)


def later_candle(offset_minutes: int, minutes: int = 15) -> Candle:
    start = datetime(2026, 9, 21, 13, 30, tzinfo=UTC) + timedelta(minutes=offset_minutes)
    return Candle(
        timeframe=Timeframe.M15, ts=start, end_ts=start + timedelta(minutes=minutes - 1),
        session_day=start.date(), open=Decimal("10.00"), high=Decimal("10.50"), low=Decimal("9.50"),
        close=Decimal("10.00"), volume=Decimal("15000"), minutes_expected=minutes,
        minutes_present=minutes, truncated=False,
    )


def test_a_window_of_one_candle_is_that_candle():
    series = [bar(index) for index in range(30)]
    assert candles_input_hash(series, [candle()]) == candle_input_hash(series, candle())


def test_a_window_covers_every_bar_of_every_candle_in_it():
    series = [bar(index) for index in range(45)]
    window = [candle(), later_candle(15), later_candle(30)]
    assert candles_input_hash(series, window) == bars_content_hash(series)


def test_correcting_a_bar_in_an_earlier_candle_of_the_window_changes_its_identity():
    """The whole reason the window exists: a reading at the last bucket depends on the earlier ones too."""
    original = [bar(index) for index in range(45)]
    corrected = [bar(3, close="10.25") if index == 3 else bar(index) for index in range(45)]
    window = [candle(), later_candle(15), later_candle(30)]
    # The corrected minute falls in the FIRST candle of the window, and the last candle's own bars are
    # untouched — which is exactly the case the single-candle hash could not see.
    assert candle_input_hash(original, later_candle(30)) == candle_input_hash(corrected, later_candle(30))
    assert candles_input_hash(original, window) != candles_input_hash(corrected, window)
