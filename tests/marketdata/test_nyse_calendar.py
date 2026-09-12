from datetime import date

from core.marketdata.nyse_calendar import load_nyse_calendar
from tests.support import et


def test_matches_known_holiday_and_half_day():
    calendar = load_nyse_calendar(date(2025, 11, 24), date(2025, 12, 3))
    days = [session.day for session in calendar.sessions]
    assert date(2025, 11, 27) not in days
    by_day = {session.day: session for session in calendar.sessions}
    assert by_day[date(2025, 11, 26)].open_utc == et("2025-11-26", "09:30")
    assert by_day[date(2025, 11, 26)].close_utc == et("2025-11-26", "16:00")
    assert by_day[date(2025, 11, 28)].close_utc == et("2025-11-28", "13:00")
    assert len(days) == 7
