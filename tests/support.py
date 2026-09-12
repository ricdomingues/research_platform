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


from decimal import Decimal

from core.domain.models import Bar, Direction, SignalSpec


def D(value) -> Decimal:
    return Decimal(str(value))


def bar(ts: datetime, o, h, l, c) -> Bar:
    return Bar(ts=ts, open=D(o), high=D(h), low=D(l), close=D(c), volume=D(1000))


def flat_bars(calendar: SessionCalendar, start: datetime, end: datetime, price) -> list[Bar]:
    return [bar(minute, price, price, price, price) for minute in calendar.expected_minutes(start, end)]


def long_signal(**overrides) -> SignalSpec:
    values = dict(
        ticker="AAPL",
        direction=Direction.LONG,
        entry_zone_low=D(100),
        entry_zone_high=D(102),
        stop=D(97),
        target1=D(106),
        target2=D(110),
        trigger_price=None,
        valid_sessions=3,
    )
    values.update(overrides)
    return SignalSpec(**values)


from core.domain.calendar import evaluation_start_ts, signal_valid_until_ts
from core.domain.models import FillConfig, OrderContext


def make_ctx(signal: SignalSpec | None = None, config: FillConfig | None = None,
             start: datetime | None = None, calendar: SessionCalendar | None = None) -> OrderContext:
    calendar = calendar or make_calendar()
    signal = signal or long_signal()
    begin = evaluation_start_ts(calendar, start or et("2025-11-25", "09:30"))
    return OrderContext(
        signal=signal,
        config=config or FillConfig(),
        calendar=calendar,
        evaluation_start_ts=begin,
        valid_until_ts=signal_valid_until_ts(calendar, begin, signal.valid_sessions),
    )
