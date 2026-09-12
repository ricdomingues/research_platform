"""Build a SessionCalendar from pandas_market_calendars (local computation, no network)."""

from __future__ import annotations

from datetime import date, timezone

import pandas_market_calendars as mcal

from core.domain.calendar import Session, SessionCalendar


def load_nyse_calendar(start: date, end: date) -> SessionCalendar:
    schedule = mcal.get_calendar("XNYS").schedule(
        start_date=start.isoformat(), end_date=end.isoformat()
    )
    sessions = [
        Session(
            day=index.date(),
            open_utc=row["market_open"].to_pydatetime().astimezone(timezone.utc),
            close_utc=row["market_close"].to_pydatetime().astimezone(timezone.utc),
        )
        for index, row in schedule.iterrows()
    ]
    return SessionCalendar(sessions)
