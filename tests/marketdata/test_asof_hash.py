from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from tests.support import bar, et
from virtual_orders.marketdata.asof import bars_in_minutes, floor_minute, selected_data_hash

DAY = "2025-11-25"
B1, B2 = UUID(int=1), UUID(int=2)


def versioned(hm: str, price, batch: UUID):
    return replace(bar(et(DAY, hm), price, price, price, price), batch_id=batch)


MINUTES = [et(DAY, "10:00"), et(DAY, "10:01"), et(DAY, "10:02")]


def test_floor_minute_is_utc_and_rejects_naive():
    assert floor_minute(et(DAY, "10:00", 59)) == et(DAY, "10:00")
    assert floor_minute(et(DAY, "10:00")).tzinfo is UTC
    with pytest.raises(ValueError):
        floor_minute(datetime(2025, 11, 25, 10, 0))


def test_hash_is_deterministic_and_marks_missing_minutes():
    bars = [versioned("10:00", 100, B1), versioned("10:02", 101, B1)]
    first = selected_data_hash("feed", "AAPL", MINUTES, bars)
    assert first == selected_data_hash("feed", "AAPL", MINUTES, list(reversed(bars)))
    filled = selected_data_hash("feed", "AAPL", MINUTES, bars + [versioned("10:01", 100, B1)])
    assert filled != first


def test_hash_changes_with_source_batch_price_and_ticker():
    base = selected_data_hash("feed", "AAPL", MINUTES[:1], [versioned("10:00", 100, B1)])
    assert base != selected_data_hash("other_feed", "AAPL", MINUTES[:1], [versioned("10:00", 100, B1)])
    assert base != selected_data_hash("feed", "AAPL", MINUTES[:1], [versioned("10:00", 100, B2)])
    assert base != selected_data_hash("feed", "AAPL", MINUTES[:1], [versioned("10:00", 100.01, B1)])
    assert base != selected_data_hash("feed", "MSFT", MINUTES[:1], [versioned("10:00", 100, B1)])
    assert selected_data_hash("feed", "AAPL", MINUTES[:1], []) != selected_data_hash("other_feed", "AAPL", MINUTES[:1], [])


def test_hash_rejects_bars_outside_minutes_duplicates_and_unversioned_bars():
    with pytest.raises(ValueError, match="outside"):
        selected_data_hash("feed", "AAPL", MINUTES[:1], [versioned("10:05", 100, B1)])
    with pytest.raises(ValueError, match="duplicate"):
        selected_data_hash("feed", "AAPL", MINUTES, [versioned("10:00", 100, B1), versioned("10:00", 100, B2)])
    with pytest.raises(ValueError, match="batch_id"):
        selected_data_hash("feed", "AAPL", MINUTES[:1], [bar(et(DAY, "10:00"), 100, 100, 100, 100)])


def test_bars_in_minutes_filters_non_expected_timestamps():
    bars = [versioned("10:00", 100, B1), versioned("10:05", 100, B1)]
    assert [b.ts for b in bars_in_minutes(bars, MINUTES)] == [et(DAY, "10:00")]
