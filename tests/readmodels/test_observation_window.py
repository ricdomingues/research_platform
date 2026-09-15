from datetime import date

import pytest

from tests.support import et
from virtual_orders.readmodels.observation_window import (
    MAX_SUMMARY_DAYS,
    ObservationRequestInvalid,
    session_window,
    summary_sessions,
)

AS_OF = et("2025-12-02", "12:00")  # a Tuesday; 2025-11-27 is Thanksgiving and 2025-11-28 closes at 13:00 ET


def test_a_session_window_owns_the_days_without_a_session_before_it():
    monday = session_window(date(2025, 12, 1), AS_OF)
    assert (monday.start, monday.end) == (et("2025-11-29", "00:00"), et("2025-12-02", "00:00"))
    assert (monday.session_open_utc, monday.session_close_utc) == (
        et("2025-12-01", "09:30"), et("2025-12-01", "16:00"))
    assert monday.complete and monday.effective_end == monday.end
    friday = session_window(date(2025, 11, 28), AS_OF)
    assert friday.start == et("2025-11-27", "00:00") and friday.session_close_utc == et("2025-11-28", "13:00")
    today = session_window(date(2025, 12, 2), AS_OF)
    assert not today.complete and today.effective_end == AS_OF


@pytest.mark.parametrize("day, codes", [
    (date(2025, 11, 27), ["NOT_A_SESSION:2025-11-27"]),
    (date(2025, 11, 29), ["NOT_A_SESSION:2025-11-29"]),
    (date(2025, 12, 3), ["DAY_IN_FUTURE:2025-12-03"]),
    (date(2025, 12, 6), ["DAY_IN_FUTURE:2025-12-06", "NOT_A_SESSION:2025-12-06"]),
])
def test_days_without_a_session_and_future_days_are_rejected_with_codes(day, codes):
    with pytest.raises(ObservationRequestInvalid) as caught:
        session_window(day, AS_OF)
    assert caught.value.codes == codes


def test_a_naive_as_of_is_a_programming_error():
    with pytest.raises(ValueError, match="timezone-aware"):
        session_window(date(2025, 12, 1), AS_OF.replace(tzinfo=None))


def test_a_summary_lists_the_sessions_of_the_range():
    windows = summary_sessions(date(2025, 11, 22), date(2025, 12, 1), AS_OF)
    assert [window.session_day for window in windows] == [
        date(2025, 11, 24), date(2025, 11, 25), date(2025, 11, 26), date(2025, 11, 28), date(2025, 12, 1)]
    assert MAX_SUMMARY_DAYS == 45


@pytest.mark.parametrize("first, last, codes", [
    (date(2025, 12, 1), date(2025, 11, 24), ["EMPTY_RANGE"]),
    (date(2025, 10, 1), date(2025, 11, 25), ["RANGE_TOO_LARGE"]),
    (date(2025, 11, 29), date(2025, 11, 30), ["NO_SESSIONS"]),
    (date(2025, 12, 1), date(2025, 12, 5), ["DAY_IN_FUTURE:2025-12-05"]),
])
def test_invalid_summary_ranges_are_rejected_with_codes(first, last, codes):
    with pytest.raises(ObservationRequestInvalid) as caught:
        summary_sessions(first, last, AS_OF)
    assert caught.value.codes == codes
