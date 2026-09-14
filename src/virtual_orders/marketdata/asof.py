"""As-of reads (spec 3.6) and the ingestion watermark (plan decision 3)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from core.domain.hashing import sha256_hex
from core.domain.models import Bar
from virtual_orders.storage.database import INGEST_LOCK_KEY

_AS_OF_SQL = text(
    """
    SELECT DISTINCT ON (b.ts) b.ts, b.open, b.high, b.low, b.close, b.volume, b.batch_id
    FROM bars_1m b
    JOIN bar_batches bb ON bb.batch_id = b.batch_id
    WHERE b.ticker = :ticker AND b.source = :source
      AND b.ts >= :start AND b.ts < :end
      AND bb.ingested_at <= :as_of
    ORDER BY b.ts, bb.ingested_at DESC, b.batch_id DESC
    """
)

_VERSION_SQL = text(
    """
    SELECT ts, open, high, low, close, volume, batch_id FROM bars_1m
    WHERE ticker = :ticker AND source = :source AND ts = :ts AND batch_id = :batch_id
    """
)


def floor_minute(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return ts.astimezone(UTC).replace(second=0, microsecond=0)


def acquire_data_as_of(engine: Engine) -> datetime:
    """A knowledge instant T such that no batch with ingested_at <= T can still be committed."""
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INGEST_LOCK_KEY})
        value: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    return value.astimezone(UTC)


def _bar(row: Any) -> Bar:
    return Bar(ts=row.ts, open=row.open, high=row.high, low=row.low, close=row.close,
               volume=row.volume, batch_id=row.batch_id)


def read_bars_as_of(
    conn: Connection, ticker: str, source: str, start: datetime, end: datetime, as_of: datetime
) -> list[Bar]:
    rows = conn.execute(
        _AS_OF_SQL,
        {"ticker": ticker, "source": source, "start": start, "end": end, "as_of": as_of},
    ).all()
    return [_bar(row) for row in rows]


def read_bar_version(conn: Connection, ticker: str, source: str, ts: datetime, batch_id: UUID) -> Bar:
    row = conn.execute(
        _VERSION_SQL, {"ticker": ticker, "source": source, "ts": ts, "batch_id": batch_id}
    ).first()
    if row is None:
        raise LookupError(f"no bar {ticker} {ts.isoformat()} in batch {batch_id}")
    return _bar(row)


def bars_in_minutes(bars: Iterable[Bar], minutes: Sequence[datetime]) -> list[Bar]:
    wanted = set(minutes)
    return [b for b in bars if b.ts in wanted]


def selected_data_hash(source: str, ticker: str, minutes: Sequence[datetime], bars: Sequence[Bar]) -> str:
    """Identity of the data a run used: source, version and OHLCV per expected minute, or MISSING (D6)."""
    by_ts: dict[datetime, Bar] = {}
    for item in bars:
        if item.batch_id is None:
            raise ValueError(f"bar {item.ts.isoformat()} has no batch_id")
        if item.ts in by_ts:
            raise ValueError(f"duplicate bar minute {item.ts.isoformat()}")
        by_ts[item.ts] = item
    outside = set(by_ts) - set(minutes)
    if outside:
        raise ValueError(f"bars outside the selected minutes: {sorted(t.isoformat() for t in outside)}")
    entries: list[list[Any]] = []
    for minute in sorted(minutes):
        found = by_ts.get(minute)
        if found is None:
            entries.append([source, ticker, minute, "MISSING"])
        else:
            entries.append([source, ticker, minute, found.batch_id, found.open, found.high, found.low,
                            found.close, found.volume])
    return sha256_hex(entries)
