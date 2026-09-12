"""Expected-minute calendar and evaluation clock (spec 3.4). Pure: sessions are injected."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

ONE_MINUTE = timedelta(minutes=1)


class CalendarRangeError(LookupError):
    """The query falls outside the loaded session range."""


def _require_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return ts.astimezone(timezone.utc)


def _is_whole_minute(ts: datetime) -> bool:
    return ts.second == 0 and ts.microsecond == 0


@dataclass(frozen=True)
class Session:
    day: date
    open_utc: datetime
    close_utc: datetime

    def __post_init__(self) -> None:
        for name in ("open_utc", "close_utc"):
            ts = getattr(self, name)
            if ts.tzinfo is None:
                raise ValueError("session bounds must be timezone-aware")
            if not _is_whole_minute(ts):
                raise ValueError("session bounds must be whole minutes")
            object.__setattr__(self, name, ts.astimezone(timezone.utc))
        if self.close_utc <= self.open_utc:
            raise ValueError("session close must be after open")


class SessionCalendar:
    def __init__(self, sessions: Sequence[Session]) -> None:
        if not sessions:
            raise ValueError("calendar needs at least one session")
        ordered = sorted(sessions, key=lambda s: s.open_utc)
        for previous, current in zip(ordered, ordered[1:]):
            if current.open_utc < previous.close_utc:
                raise ValueError("sessions overlap")
        self._sessions = tuple(ordered)
        self._opens = [s.open_utc for s in ordered]
        self._closes = [s.close_utc for s in ordered]

    @property
    def sessions(self) -> tuple[Session, ...]:
        return self._sessions

    def _check_lower(self, ts: datetime) -> None:
        if ts < self._sessions[0].open_utc - timedelta(hours=24):
            raise CalendarRangeError(f"{ts.isoformat()} is before the loaded calendar")

    def session_containing(self, ts: datetime) -> Session | None:
        ts = _require_utc(ts)
        self._check_lower(ts)
        index = bisect.bisect_right(self._closes, ts)
        if index < len(self._sessions) and self._sessions[index].open_utc <= ts:
            return self._sessions[index]
        if index >= len(self._sessions):
            raise CalendarRangeError(f"{ts.isoformat()} is after the loaded calendar")
        return None

    def is_expected_minute(self, ts: datetime) -> bool:
        ts = _require_utc(ts)
        if not _is_whole_minute(ts):
            return False
        if ts > self._sessions[-1].close_utc:
            raise CalendarRangeError(f"{ts.isoformat()} is after the loaded calendar")
        if ts == self._sessions[-1].close_utc:
            return False
        try:
            return self.session_containing(ts) is not None
        except CalendarRangeError:
            return False

    def first_expected_minute_at_or_after(self, ts: datetime) -> datetime:
        ts = _require_utc(ts)
        self._check_lower(ts)
        minute = ts.replace(second=0, microsecond=0)
        if minute < ts:
            minute += ONE_MINUTE
        index = bisect.bisect_right(self._closes, minute)
        if index >= len(self._sessions):
            raise CalendarRangeError(f"no session at or after {ts.isoformat()}")
        return max(minute, self._sessions[index].open_utc)

    def next_expected_minute(self, ts: datetime) -> datetime:
        ts = _require_utc(ts)
        return self.first_expected_minute_at_or_after(ts.replace(second=0, microsecond=0) + ONE_MINUTE)

    def expected_minutes(self, start: datetime, end: datetime) -> list[datetime]:
        start, end = _require_utc(start), _require_utc(end)
        minutes: list[datetime] = []
        if end > self._sessions[-1].close_utc:
            raise CalendarRangeError(f"{end.isoformat()} is after the loaded calendar")
        if end <= start:
            return minutes
        try:
            minute = self.first_expected_minute_at_or_after(start)
        except CalendarRangeError:
            return minutes  # start is past the last expected minute and end <= last close
        while minute < end:
            minutes.append(minute)
            try:
                minute = self.next_expected_minute(minute)
            except CalendarRangeError:
                break  # only the final minute of the last session; end <= its close
        return minutes

    def nth_session_close(self, first: Session, n: int) -> datetime:
        if n < 1:
            raise ValueError("n must be >= 1")
        index = self._sessions.index(first) + n - 1
        if index >= len(self._sessions):
            raise CalendarRangeError(f"session {n} after {first.day} is not loaded")
        return self._sessions[index].close_utc

    def last_expected_minute_before(self, ts: datetime) -> datetime:
        ts = _require_utc(ts)
        index = bisect.bisect_left(self._opens, ts) - 1
        if index < 0:
            raise CalendarRangeError(f"no expected minute before {ts.isoformat()}")
        candidate = min(ts, self._sessions[index].close_utc)
        floored = candidate.replace(second=0, microsecond=0)
        return floored - ONE_MINUTE if floored == candidate else floored


def evaluation_start_ts(calendar: SessionCalendar, created_at: datetime) -> datetime:
    """First complete candle that starts at or after the decision time (spec 3.4)."""
    return calendar.first_expected_minute_at_or_after(created_at)


def signal_valid_until_ts(
    calendar: SessionCalendar, signal_evaluation_start: datetime, valid_sessions: int
) -> datetime:
    """Session 1 is the regular session containing the signal's evaluation start."""
    if not 1 <= valid_sessions <= 20:
        raise ValueError("valid_sessions must be between 1 and 20")
    session = calendar.session_containing(signal_evaluation_start)
    if session is None:
        raise ValueError("signal evaluation start must be an expected minute")
    return calendar.nth_session_close(session, valid_sessions)
