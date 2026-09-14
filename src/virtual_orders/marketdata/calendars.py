"""NYSE calendars padded around a window (Plan 1 close-out entry 7)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from core.domain.calendar import SessionCalendar
from core.marketdata.nyse_calendar import load_nyse_calendar

CALENDAR_PAD = timedelta(days=10)


@lru_cache(maxsize=128)
def _load(start: date, end: date) -> SessionCalendar:
    return load_nyse_calendar(start, end)


def calendar_for_window(earliest: datetime, latest: datetime) -> SessionCalendar:
    """At least one session before `earliest` and one after `latest` are always loaded."""
    if earliest.tzinfo is None or latest.tzinfo is None:
        raise ValueError("calendar window bounds must be timezone-aware")
    start = (earliest.astimezone(UTC) - CALENDAR_PAD).date()
    end = (latest.astimezone(UTC) + CALENDAR_PAD).date()
    return _load(start, end)
