from datetime import date

import pytest

from core.domain.calendar import (
    CalendarRangeError,
    Session,
    SessionCalendar,
    evaluation_start_ts,
    signal_valid_until_ts,
)
from tests.support import et, make_calendar

CAL = make_calendar()


@pytest.mark.parametrize(
    ("created", "expected"),
    [
        (et("2025-11-25", "08:00"), et("2025-11-25", "09:30")),
        (et("2025-11-25", "10:30"), et("2025-11-25", "10:30")),
        (et("2025-11-25", "10:30", 18), et("2025-11-25", "10:31")),
        (et("2025-11-26", "15:59", 30), et("2025-11-28", "09:30")),  # véspera de feriado
        (et("2025-11-28", "12:59", 30), et("2025-12-01", "09:30")),  # meio pregão
        (et("2025-11-27", "11:00"), et("2025-11-28", "09:30")),      # feriado
        (et("2025-11-25", "16:30"), et("2025-11-26", "09:30")),      # após fechamento
    ],
)
def test_evaluation_start_ts(created, expected):
    assert evaluation_start_ts(CAL, created) == expected


def test_next_expected_minute_crosses_close_holiday_and_half_day():
    assert CAL.next_expected_minute(et("2025-11-25", "10:00")) == et("2025-11-25", "10:01")
    assert CAL.next_expected_minute(et("2025-11-26", "15:59")) == et("2025-11-28", "09:30")
    assert CAL.next_expected_minute(et("2025-11-28", "12:59")) == et("2025-12-01", "09:30")
    assert CAL.next_expected_minute(et("2025-11-25", "10:00", 20)) == et("2025-11-25", "10:01")


def test_expected_minutes_and_membership():
    half_day = CAL.expected_minutes(et("2025-11-28", "00:00"), et("2025-11-28", "23:00"))
    assert len(half_day) == 210
    assert half_day[0] == et("2025-11-28", "09:30")
    assert half_day[-1] == et("2025-11-28", "12:59")
    assert CAL.is_expected_minute(et("2025-11-25", "15:59"))
    assert not CAL.is_expected_minute(et("2025-11-25", "16:00"))
    assert not CAL.is_expected_minute(et("2025-11-27", "12:30"))
    assert not CAL.is_expected_minute(et("2025-11-25", "10:00", 1))
    two_days = CAL.expected_minutes(et("2025-11-26", "15:58"), et("2025-11-28", "09:32"))
    assert two_days == [
        et("2025-11-26", "15:58"),
        et("2025-11-26", "15:59"),
        et("2025-11-28", "09:30"),
        et("2025-11-28", "09:31"),
    ]


def test_session_containing():
    session = CAL.session_containing(et("2025-11-28", "12:00"))
    assert session is not None and session.day == date(2025, 11, 28)
    assert CAL.session_containing(et("2025-11-28", "13:00")) is None


def test_signal_valid_until_counts_current_session():
    start = et("2025-11-26", "10:31")
    assert signal_valid_until_ts(CAL, start, 1) == et("2025-11-26", "16:00")
    assert signal_valid_until_ts(CAL, start, 2) == et("2025-11-28", "13:00")
    assert signal_valid_until_ts(CAL, start, 3) == et("2025-12-01", "16:00")


def test_signal_valid_until_rejects_bad_input():
    with pytest.raises(ValueError):
        signal_valid_until_ts(CAL, et("2025-11-26", "10:31"), 0)
    with pytest.raises(ValueError):
        signal_valid_until_ts(CAL, et("2025-11-26", "16:30"), 1)
    with pytest.raises(CalendarRangeError):
        signal_valid_until_ts(CAL, et("2025-12-03", "10:00"), 5)


def test_last_expected_minute_before():
    assert CAL.last_expected_minute_before(et("2025-11-28", "13:00")) == et("2025-11-28", "12:59")
    assert CAL.last_expected_minute_before(et("2025-11-25", "10:00", 30)) == et("2025-11-25", "10:00")
    assert CAL.last_expected_minute_before(et("2025-11-25", "10:00")) == et("2025-11-25", "09:59")


def test_out_of_range_raises():
    with pytest.raises(CalendarRangeError):
        CAL.first_expected_minute_at_or_after(et("2025-11-20", "10:00"))
    with pytest.raises(CalendarRangeError):
        CAL.next_expected_minute(et("2025-12-03", "15:59"))


def test_naive_datetime_and_invalid_sessions_rejected():
    from datetime import datetime

    with pytest.raises(ValueError):
        CAL.first_expected_minute_at_or_after(datetime(2025, 11, 25, 10, 0))
    with pytest.raises(ValueError):
        SessionCalendar([])
    with pytest.raises(ValueError):
        Session(date(2025, 11, 25), et("2025-11-25", "16:00"), et("2025-11-25", "09:30"))
    overlapping = [
        Session(date(2025, 11, 25), et("2025-11-25", "09:30"), et("2025-11-25", "16:00")),
        Session(date(2025, 11, 25), et("2025-11-25", "15:00"), et("2025-11-25", "17:00")),
    ]
    with pytest.raises(ValueError):
        SessionCalendar(overlapping)


def test_is_expected_minute_raises_past_loaded_range():
    assert CAL.is_expected_minute(et("2025-12-03", "15:59"))
    assert not CAL.is_expected_minute(et("2025-12-03", "16:00"))
    assert not CAL.is_expected_minute(et("2025-12-04", "10:00", 30))
    with pytest.raises(CalendarRangeError):
        CAL.is_expected_minute(et("2025-12-04", "10:00"))


def test_expected_minutes_raises_when_end_past_loaded_range():
    with pytest.raises(CalendarRangeError):
        CAL.expected_minutes(et("2025-12-03", "15:00"), et("2025-12-03", "16:01"))
    with pytest.raises(CalendarRangeError):
        CAL.expected_minutes(et("2025-12-03", "15:00"), et("2025-12-04", "10:00"))


def test_expected_minutes_reaches_final_minute_of_last_session():
    minutes = CAL.expected_minutes(et("2025-12-03", "15:00"), et("2025-12-03", "16:00"))
    assert len(minutes) == 60
    assert minutes[-1] == et("2025-12-03", "15:59")


def test_order_context_requires_a_session_after_validity():
    from core.domain.models import FillConfig, OrderContext
    from tests.support import long_signal

    begin = et("2025-12-03", "09:30")
    with pytest.raises(ValueError, match="calendar must cover at least one session after valid_until_ts"):
        OrderContext(long_signal(valid_sessions=1), FillConfig(), CAL, begin, et("2025-12-03", "16:00"))
    ctx = OrderContext(long_signal(valid_sessions=1), FillConfig(), CAL, et("2025-12-02", "09:30"),
                       et("2025-12-02", "16:00"))
    assert ctx.valid_until_ts == et("2025-12-02", "16:00")
