"""Deterministic resampling of stored 1-minute bars into higher timeframes (Plan 5, D74).

Pure: the NYSE `SessionCalendar` is injected, exactly as `core.dataquality` injects it. Nothing here reads the
database or calls a provider.

Conventions, all of them tested:

* The canonical data stays `bars_1m`. A higher timeframe is only ever a view over expected session minutes.
* Buckets are anchored at the **session open** and counted in *expected minutes*, never in wall-clock hours.
  A regular session gives 09:30-10:29, 10:30-11:29, ... for 1h; a half day simply produces fewer buckets and a
  shorter final one (`truncated`). A bucket therefore never spans two sessions, a weekend or a holiday.
* `Timeframe.D1` is one whole regular session, however long that session is.
* A candle is emitted only once **every minute of its bucket has elapsed** (`bucket_end + 1min <=
  completed_through`), so a timeframe candle is never built from bars that are still arriving.
* `open` = first present bar's open, `high`/`low` the extremes, `close` = last present bar's close,
  `volume` the sum. Minutes with no stored bar are counted in `minutes_expected - minutes_present` rather than
  silently ignored; a bucket with no bar at all produces no candle.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from core.domain.calendar import ONE_MINUTE, Session, SessionCalendar
from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Bar
from virtual_orders.research.models import Candle, Timeframe, bucket_minutes

ZERO = Decimal(0)


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def session_buckets(calendar: SessionCalendar, session: Session, timeframe: Timeframe) -> list[list[datetime]]:
    """The expected minutes of `session`, chunked into the buckets of `timeframe` from the session open."""
    minutes = calendar.expected_minutes(session.open_utc, session.close_utc)
    size = bucket_minutes(timeframe)
    if size is None:
        return [minutes] if minutes else []
    return [minutes[index : index + size] for index in range(0, len(minutes), size)]


def sessions_in_window(calendar: SessionCalendar, start: datetime, end: datetime) -> list[Session]:
    """Every loaded session whose regular hours intersect `[start, end)`."""
    start, end = _utc(start, "start"), _utc(end, "end")
    return [item for item in calendar.sessions if item.open_utc < end and item.close_utc > start]


def _by_minute(bars: Sequence[Bar]) -> dict[datetime, Bar]:
    indexed: dict[datetime, Bar] = {}
    for item in bars:
        if item.ts in indexed:
            raise ValueError(f"duplicate bar minute {item.ts.isoformat()}")
        indexed[item.ts] = item
    return indexed


def _candle(
    timeframe: Timeframe, session: Session, bucket: Sequence[datetime], present: Sequence[Bar], nominal: int | None
) -> Candle:
    with localcontext(CANONICAL_CONTEXT):
        volume = sum((item.volume for item in present), ZERO)
    return Candle(
        timeframe=timeframe,
        ts=bucket[0],
        end_ts=bucket[-1],
        session_day=session.day,
        open=present[0].open,
        high=max(item.high for item in present),
        low=min(item.low for item in present),
        close=present[-1].close,
        volume=volume,
        minutes_expected=len(bucket),
        minutes_present=len(present),
        truncated=nominal is not None and len(bucket) < nominal,
    )


def resample(
    bars: Sequence[Bar],
    *,
    calendar: SessionCalendar,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    completed_through: datetime,
) -> list[Candle]:
    """Completed `timeframe` candles of the sessions intersecting `[start, end)`, in chronological order.

    `completed_through` is the analysis instant: a bucket is emitted only when its last expected minute has
    fully elapsed by then. Bars outside the expected minutes of a session (extended hours, stray rows) are
    ignored; a duplicated minute is an error, never a silent overwrite.
    """
    completed_through = _utc(completed_through, "completed_through")
    indexed = _by_minute(bars)
    candles: list[Candle] = []
    nominal = bucket_minutes(timeframe)
    for session in sessions_in_window(calendar, start, end):
        for bucket in session_buckets(calendar, session, timeframe):
            if bucket[-1] + ONE_MINUTE > completed_through:
                break  # this bucket, and every later one of this session, is still open
            present = [indexed[minute] for minute in bucket if minute in indexed]
            if not present:
                continue  # no stored bar at all in the bucket: a gap, never a fabricated candle
            candles.append(_candle(timeframe, session, bucket, present, nominal))
    return candles


def last_completed_minute(calendar: SessionCalendar, now: datetime) -> datetime | None:
    """The last expected minute whose own candle is closed at `now`, or None before the first loaded session."""
    now = _utc(now, "now")
    try:
        return calendar.last_expected_minute_before(now)
    except LookupError:
        return None


def settled(candle: Candle, *, completed_through: datetime, settle: timedelta) -> bool:
    """Whether this candle may be read as final, or is still waiting for bars that have not arrived.

    A bucket is emitted by `resample` once its last expected minute has *elapsed*, which is not the same as
    every bar of it having been *ingested*: the watchlist ingests on its own cadence, so a scan reading a
    just-closed bucket sees a candle whose close is still moving. During the canary this was not theoretical
    — 7 of 14 candidates were built on candles that all changed once their bars landed, and one pattern
    (a SHOOTING_STAR, the session's highest-scoring detection) ceased to exist against the corrected close.
    The damage outlived the correction, because a detection advances `last_detection_end_ts` past its own
    candle and the bucket is then never re-read.

    A candle with every expected minute present is final whenever it closed. One still missing minutes is
    given `settle` past its close for them to arrive; beyond that the minutes are genuinely absent rather
    than late — an illiquid minute with no trade never gets a bar — and the candle is read as it stands,
    with `minutes_present` recording what it was built from.
    """
    if candle.complete:
        return True
    return bool(candle.end_ts + ONE_MINUTE + settle <= completed_through)


def iter_completed(candles: Sequence[Candle]) -> Iterator[Candle]:
    """Only candles whose every expected minute had a stored bar (`minutes_present == minutes_expected`)."""
    return (candle for candle in candles if candle.complete)
