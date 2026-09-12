from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from core.domain.calendar import Session, SessionCalendar

ET = ZoneInfo("America/New_York")

FULL_DAYS = ["2025-11-24", "2025-11-25", "2025-11-26", "2025-12-01", "2025-12-02", "2025-12-03"]
HALF_DAYS = ["2025-11-28"]


def et(day: str, hm: str, second: int = 0) -> datetime:
    year, month, dom = (int(part) for part in day.split("-"))
    hour, minute = (int(part) for part in hm.split(":"))
    return datetime(year, month, dom, hour, minute, second, tzinfo=ET).astimezone(timezone.utc)


def make_calendar() -> SessionCalendar:
    sessions = [Session(date.fromisoformat(d), et(d, "09:30"), et(d, "16:00")) for d in FULL_DAYS]
    sessions += [Session(date.fromisoformat(d), et(d, "09:30"), et(d, "13:00")) for d in HALF_DAYS]
    return SessionCalendar(sessions)
