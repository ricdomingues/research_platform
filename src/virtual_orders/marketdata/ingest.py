"""Versioned, deduplicated ingestion into bar_batches + bars_1m (spec 3.1, 3.6; plan decisions 3 and 6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from core.domain.hashing import sha256_hex
from core.domain.models import Bar
from virtual_orders.marketdata.asof import read_bars_as_of
from virtual_orders.marketdata.sources import BarSource, RawBar, SourceDataError
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.database import INGEST_LOCK_KEY
from virtual_orders.storage.tables import bar_batches, bars_1m


@dataclass(frozen=True)
class IngestResult:
    batch_id: UUID | None
    fetched: int
    stored: int


def _validated(ticker: str, start: datetime, end: datetime, bars: Sequence[RawBar]) -> list[RawBar]:
    seen: set[datetime] = set()
    for item in bars:
        if item.ticker != ticker:
            raise SourceDataError(f"ticker mismatch: expected {ticker}, got {item.ticker}")
        if not start <= item.ts < end:
            raise SourceDataError(f"bar {item.ts.isoformat()} outside [{start.isoformat()}, {end.isoformat()})")
        if item.ts in seen:
            raise SourceDataError(f"duplicate bar minute {item.ts.isoformat()}")
        seen.add(item.ts)
        try:
            item.to_bar()
        except (TypeError, ValueError) as exc:
            raise SourceDataError(f"invalid bar {item.ts.isoformat()}: {exc}") from exc
    return sorted(bars, key=lambda b: b.ts)


def _same(item: RawBar, current: Bar) -> bool:
    return (item.open, item.high, item.low, item.close, item.volume) == (
        current.open, current.high, current.low, current.close, current.volume
    )


def store_batch(
    engine: Engine, source: BarSource, ticker: str, start: datetime, end: datetime, bars: Sequence[RawBar]
) -> IngestResult:
    ordered = _validated(ticker, start, end, bars)
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INGEST_LOCK_KEY})
        ingested_at: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        current = {b.ts: b for b in read_bars_as_of(conn, ticker, source.source, start, end, ingested_at)}
        changed = [b for b in ordered if b.ts not in current or not _same(b, current[b.ts])]
        if not changed:
            return IngestResult(None, len(ordered), 0)
        batch_id = uuid4()
        conn.execute(bar_batches.insert().values(
            batch_id=batch_id,
            provider=source.provider,
            provider_version=source.provider_version,
            data_tier=source.data_tier.value,
            request=to_document({"ticker": ticker, "start": start, "end": end, "source": source.source}),
            content_hash=sha256_hex([[b.ticker, b.ts, b.open, b.high, b.low, b.close, b.volume] for b in changed]),
            ingested_at=ingested_at,
        ))
        conn.execute(bars_1m.insert(), [
            {"ticker": b.ticker, "ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume, "source": source.source, "batch_id": batch_id}
            for b in changed
        ])
    return IngestResult(batch_id, len(ordered), len(changed))


def ingest_bars(engine: Engine, source: BarSource, ticker: str, start: datetime, end: datetime) -> IngestResult:
    """Network fetch happens outside the database transaction."""
    if end <= start:
        return IngestResult(None, 0, 0)
    return store_batch(engine, source, ticker, start, end, source.fetch_bars(ticker, start, end))
