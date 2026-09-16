"""The session window of the daily observation report (Plan 4, D61, D63): NYSE sessions only, never a provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.domain.calendar import Session
from virtual_orders.marketdata.calendars import calendar_for_window

MARKET_TZ = ZoneInfo("America/New_York")
MAX_SUMMARY_DAYS = 45
NOT_A_SESSION = "NOT_A_SESSION"
DAY_IN_FUTURE = "DAY_IN_FUTURE"
EMPTY_RANGE = "EMPTY_RANGE"
RANGE_TOO_LARGE = "RANGE_TOO_LARGE"
NO_SESSIONS = "NO_SESSIONS"


class ObservationRequestInvalid(Exception):
    """Fixed codes only (D63); never a caller-supplied value beyond an ISO date."""

    def __init__(self, codes: list[str]) -> None:
        super().__init__(", ".join(codes))
        self.codes = codes


@dataclass(frozen=True)
class ObservationWindow:
    session_day: date
    session_open_utc: datetime
    session_close_utc: datetime
    start: datetime  # 00:00 ET of the day after the previous NYSE session (D61)
    end: datetime  # 00:00 ET of the day after session_day
    as_of: datetime  # database clock at the request (acquire_data_as_of)

    @property
    def effective_end(self) -> datetime:
        return min(self.end, self.as_of)

    @property
    def complete(self) -> bool:
        return self.as_of >= self.end


def _midnight_et(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=MARKET_TZ).astimezone(UTC)


def _probe(day: date) -> datetime:
    return datetime.combine(day, time(12), tzinfo=UTC)


def _sessions(first: date, last: date) -> tuple[Session, ...]:
    return calendar_for_window(_probe(first), _probe(last)).sessions  # padded: one session before and after


def session_window(day: date, as_of: datetime) -> ObservationWindow:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    codes: list[str] = []
    if day > as_of.astimezone(MARKET_TZ).date():
        codes.append(f"{DAY_IN_FUTURE}:{day.isoformat()}")
    sessions = _sessions(day, day)
    session = next((item for item in sessions if item.day == day), None)
    if session is None:
        codes.append(f"{NOT_A_SESSION}:{day.isoformat()}")
    if codes or session is None:
        raise ObservationRequestInvalid(codes)
    previous = max(item.day for item in sessions if item.day < day)
    return ObservationWindow(
        session_day=day, session_open_utc=session.open_utc, session_close_utc=session.close_utc,
        start=_midnight_et(previous + timedelta(days=1)), end=_midnight_et(day + timedelta(days=1)),
        as_of=as_of.astimezone(UTC),
    )


def summary_sessions(first: date, last: date, as_of: datetime) -> list[ObservationWindow]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    codes: list[str] = []
    if last < first:
        codes.append(EMPTY_RANGE)
    elif (last - first).days + 1 > MAX_SUMMARY_DAYS:
        codes.append(RANGE_TOO_LARGE)
    if last > as_of.astimezone(MARKET_TZ).date():
        codes.append(f"{DAY_IN_FUTURE}:{last.isoformat()}")
    if codes:
        raise ObservationRequestInvalid(codes)
    days = [item.day for item in _sessions(first, last) if first <= item.day <= last]
    if not days:
        raise ObservationRequestInvalid([NO_SESSIONS])
    return [session_window(day, as_of) for day in days]
