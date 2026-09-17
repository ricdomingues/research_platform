"""Timeframe aggregation: session boundaries, half days, incomplete candles and timestamps (spec 24).

The calendar is the synthetic one from `tests/support.py`, which already contains a half day (2025-11-28,
09:30-13:00 ET) and a holiday gap. Aggregation is the subject here, so the sessions are given, not discovered.
"""

from datetime import timedelta

import pytest

from tests.support import D, bar, et, make_calendar
from virtual_orders.research.models import Timeframe, bucket_minutes
from virtual_orders.research.timeframes import iter_completed, resample, session_buckets, sessions_in_window

CALENDAR = make_calendar()
FULL = next(item for item in CALENDAR.sessions if item.day.isoformat() == "2025-11-25")
HALF = next(item for item in CALENDAR.sessions if item.day.isoformat() == "2025-11-28")


def minutes_of(session):
    return CALENDAR.expected_minutes(session.open_utc, session.close_utc)


def flat_bars(session, price=100):
    return [bar(minute, price, price + 1, price - 1, price) for minute in minutes_of(session)]


def candles(bars, timeframe, session=FULL, through=None):
    return resample(bars, calendar=CALENDAR, timeframe=timeframe, start=session.open_utc,
                    end=session.close_utc, completed_through=through or session.close_utc)


def test_a_regular_session_has_390_expected_minutes_and_a_half_day_210():
    assert len(minutes_of(FULL)) == 390
    assert len(minutes_of(HALF)) == 210


@pytest.mark.parametrize("timeframe, full, half", [
    (Timeframe.M5, 78, 42), (Timeframe.M15, 26, 14), (Timeframe.M30, 13, 7),
    (Timeframe.H1, 7, 4), (Timeframe.H4, 2, 1), (Timeframe.D1, 1, 1),
])
def test_bucket_counts_of_a_full_session_and_of_a_half_day(timeframe, full, half):
    assert len(candles(flat_bars(FULL), timeframe)) == full
    assert len(candles(flat_bars(HALF), timeframe, HALF)) == half


def test_the_last_bucket_of_a_session_is_marked_truncated_rather_than_padded():
    hourly = candles(flat_bars(FULL), Timeframe.H1)
    assert [item.minutes_expected for item in hourly] == [60, 60, 60, 60, 60, 60, 30]
    assert [item.truncated for item in hourly] == [False] * 6 + [True]
    # A half day is shorter, not broken: its own last bucket is the truncated one.
    half_hourly = candles(flat_bars(HALF), Timeframe.H1, HALF)
    assert [item.minutes_expected for item in half_hourly] == [60, 60, 60, 30]
    # The daily candle of a half day spans that session exactly and is not "truncated": the session was short.
    (daily,) = candles(flat_bars(HALF), Timeframe.D1, HALF)
    assert (daily.minutes_expected, daily.truncated) == (210, False)


def test_a_bucket_never_spans_two_sessions():
    bars = flat_bars(FULL) + flat_bars(HALF, price=200)
    spanning = resample(bars, calendar=CALENDAR, timeframe=Timeframe.H4, start=FULL.open_utc,
                        end=HALF.close_utc, completed_through=HALF.close_utc)
    assert [(item.session_day.isoformat(), item.minutes_expected) for item in spanning] == [
        ("2025-11-25", 240), ("2025-11-25", 150), ("2025-11-28", 210)]
    # Thanksgiving (11-27) has no session at all, so nothing is produced for it and no bucket bridges the gap.
    assert {item.session_day.isoformat() for item in spanning} == {"2025-11-25", "2025-11-28"}


def test_timestamps_are_the_first_and_last_expected_minute_of_the_bucket():
    first, second = candles(flat_bars(FULL), Timeframe.M15)[:2]
    assert (first.ts, first.end_ts) == (et("2025-11-25", "09:30"), et("2025-11-25", "09:44"))
    assert (second.ts, second.end_ts) == (et("2025-11-25", "09:45"), et("2025-11-25", "09:59"))
    # The candle covers [ts, end_ts + 1min): the last minute is included, the next bucket starts after it.
    assert second.ts - first.end_ts == timedelta(minutes=1)
    assert candles(flat_bars(FULL), Timeframe.D1)[0].ts == FULL.open_utc


def test_ohlcv_is_first_open_extremes_last_close_and_summed_volume():
    minutes = minutes_of(FULL)[:15]
    bars = [
        bar(minutes[0], 100, 101, 99, "100.5"),
        bar(minutes[7], 105, 110, 104, 106),  # the high of the bucket
        bar(minutes[14], 103, 104, 95, "97.5"),  # the low, and the closing minute
    ]
    (candle,) = candles(bars, Timeframe.M15)
    assert (candle.open, candle.high, candle.low, candle.close) == (D(100), D(110), D(95), D("97.5"))
    assert candle.volume == D(3000)  # three bars of 1000
    assert (candle.minutes_expected, candle.minutes_present) == (15, 3)
    assert candle.complete is False  # twelve minutes had no stored bar, and that is recorded


def test_a_bucket_with_no_stored_bar_produces_no_candle():
    minutes = minutes_of(FULL)
    bars = [bar(minute, 100, 101, 99, 100) for minute in minutes[:15] + minutes[30:45]]
    produced = candles(bars, Timeframe.M15)
    assert [item.ts for item in produced] == [minutes[0], minutes[30]]  # the empty 09:45 bucket is absent
    assert all(item.complete for item in produced)


def test_only_candles_whose_every_minute_elapsed_are_emitted():
    bars = flat_bars(FULL)
    by_ten = candles(bars, Timeframe.M15, through=et("2025-11-25", "10:00"))
    assert [item.end_ts for item in by_ten] == [et("2025-11-25", "09:44"), et("2025-11-25", "09:59")]
    # One minute earlier the 09:45-09:59 candle is still forming and must not exist yet.
    assert [item.end_ts for item in candles(bars, Timeframe.M15, through=et("2025-11-25", "09:59"))] == [
        et("2025-11-25", "09:44")]
    assert candles(bars, Timeframe.D1, through=et("2025-11-25", "15:59")) == []


def test_completed_candles_can_be_filtered_from_partial_ones():
    minutes = minutes_of(FULL)
    bars = [bar(minute, 100, 101, 99, 100) for minute in minutes[:15]] + [bar(minutes[15], 100, 101, 99, 100)]
    produced = candles(bars, Timeframe.M15)
    assert [item.complete for item in produced] == [True, False]
    assert [item.ts for item in iter_completed(produced)] == [minutes[0]]


def test_bars_outside_the_expected_minutes_are_ignored_and_duplicates_are_an_error():
    extended = bar(et("2025-11-25", "08:00"), 100, 101, 99, 100)  # pre-market: not an expected minute
    bars = [extended, *flat_bars(FULL)]
    assert len(candles(bars, Timeframe.D1)) == 1
    assert candles(bars, Timeframe.D1)[0].minutes_present == 390  # the stray bar was not counted
    with pytest.raises(ValueError, match="duplicate bar minute"):
        candles([*flat_bars(FULL), flat_bars(FULL)[0]], Timeframe.M15)


def test_session_buckets_and_window_helpers():
    assert len(session_buckets(CALENDAR, FULL, Timeframe.M30)) == 13
    assert bucket_minutes(Timeframe.D1) is None and bucket_minutes(Timeframe.H4) == 240
    window = sessions_in_window(CALENDAR, FULL.open_utc, HALF.close_utc)
    assert [item.day.isoformat() for item in window] == ["2025-11-25", "2025-11-26", "2025-11-28"]


def test_naive_instants_are_refused():
    from datetime import datetime

    with pytest.raises(ValueError, match="timezone-aware"):
        resample([], calendar=CALENDAR, timeframe=Timeframe.M15, start=FULL.open_utc, end=FULL.close_utc,
                 completed_through=datetime(2025, 11, 25, 16, 0))  # noqa: DTZ001
