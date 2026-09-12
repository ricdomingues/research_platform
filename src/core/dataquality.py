"""Pure data-quality checks (spec 4.6). Reference data (yfinance) is injected by the caller."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Iterable, Mapping, Sequence

from core.domain.calendar import ONE_MINUTE, SessionCalendar
from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Bar, Event, EventType, OrderContext, OrderState, SignalSpec

HUNDRED = Decimal(100)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class ReviewFlag:
    reason: str
    ref: str


@dataclass(frozen=True)
class SessionQuality:
    day: date
    expected: int
    missing: tuple[datetime, ...]

    @property
    def coverage_pct(self) -> Decimal:
        present = self.expected - len(self.missing)
        with localcontext(CANONICAL_CONTEXT):
            return (Decimal(present) * HUNDRED / Decimal(self.expected)).quantize(CENT)

    def event(self) -> Event:
        return Event(
            EventType.DATA_QUALITY,
            f"DATA_QUALITY:{self.day.isoformat()}",
            payload={
                "session_date": self.day,
                "expected_bars": self.expected,
                "missing_bars": len(self.missing),
                "coverage_pct": self.coverage_pct,
                "missing_minutes": list(self.missing),
            },
        )


def quality_window(state: OrderState, ctx: OrderContext, now: datetime) -> tuple[datetime, datetime]:
    start = ctx.evaluation_start_ts
    if state.final_event_ts is not None:
        final = state.final_event_ts
        end = final + ONE_MINUTE if ctx.calendar.is_expected_minute(final) else final
    else:
        end = min(now.replace(second=0, microsecond=0), ctx.valid_until_ts)
    return start, max(start, end)


def session_quality(
    calendar: SessionCalendar, start: datetime, end: datetime, present: Iterable[datetime]
) -> list[SessionQuality]:
    present_set = set(present)
    expected: dict[date, int] = {}
    missing: dict[date, list[datetime]] = {}
    for minute in calendar.expected_minutes(start, end):
        session = calendar.session_containing(minute)
        assert session is not None
        expected[session.day] = expected.get(session.day, 0) + 1
        bucket = missing.setdefault(session.day, [])
        if minute not in present_set:
            bucket.append(minute)
    return [SessionQuality(day, count, tuple(missing[day])) for day, count in expected.items()]


def find_gaps(
    calendar: SessionCalendar, missing: Sequence[datetime], min_minutes: int
) -> list[tuple[datetime, int]]:
    gaps: list[tuple[datetime, int]] = []
    run_start: datetime | None = None
    run_length = 0
    previous: datetime | None = None
    for minute in sorted(missing):
        if previous is not None and calendar.next_expected_minute(previous) == minute:
            run_length += 1
        else:
            if run_start is not None and run_length >= min_minutes:
                gaps.append((run_start, run_length))
            run_start, run_length = minute, 1
        previous = minute
    if run_start is not None and run_length >= min_minutes:
        gaps.append((run_start, run_length))
    return gaps


def gap_events(gaps: Iterable[tuple[datetime, int]]) -> list[Event]:
    return [
        Event(
            EventType.DATA_GAP,
            f"DATA_GAP:{start.isoformat()}",
            payload={"gap_start_ts": start, "minutes": minutes},
        )
        for start, minutes in gaps
    ]


def active_levels(signal: SignalSpec, state: OrderState, minute: datetime) -> list[Decimal]:
    stop = state.stop_current if state.stop_current is not None else signal.stop
    if state.stop_active_from is not None and minute < state.stop_active_from and state.stop_previous is not None:
        stop = state.stop_previous
    levels = [
        signal.entry_zone_low,
        signal.entry_zone_high,
        stop,
        signal.target1,
        signal.target2,
        signal.trigger_price,
    ]
    return [level for level in levels if level is not None]


def missing_bar_reviews(
    missing: Iterable[datetime],
    reference: Mapping[datetime, Bar],
    signal: SignalSpec,
    state: OrderState,
) -> list[ReviewFlag]:
    flags: list[ReviewFlag] = []
    for minute in sorted(missing):
        reference_bar = reference.get(minute)
        if reference_bar is None:
            flags.append(ReviewFlag("MISSING_BAR_UNVERIFIABLE", minute.isoformat()))
        elif any(reference_bar.low <= level <= reference_bar.high
                 for level in active_levels(signal, state, minute)):
            flags.append(ReviewFlag("MISSING_BAR_LEVEL_TOUCH", minute.isoformat()))
    return flags


def daily_range_mismatch(
    bars: Iterable[Bar], reference_high: Decimal, reference_low: Decimal, tolerance_pct: Decimal
) -> bool:
    collected = list(bars)
    if not collected:
        return False
    high = max(b.high for b in collected)
    low = min(b.low for b in collected)
    with localcontext(CANONICAL_CONTEXT):
        tolerance = tolerance_pct / HUNDRED
        return (
            abs(high - reference_high) > reference_high * tolerance
            or abs(low - reference_low) > reference_low * tolerance
        )


def dividends_agree(first: Decimal | None, second: Decimal | None, tolerance: Decimal) -> bool:
    return first is not None and second is not None and abs(first - second) <= tolerance
