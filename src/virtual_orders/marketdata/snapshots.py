"""market_data_snapshots (spec 3.6): a pinned, hashed selection of bar versions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, select

from core.domain.hashing import sha256_hex
from virtual_orders.marketdata.asof import read_bars_as_of
from virtual_orders.storage.tables import market_data_snapshots


def manifest_hash(entries: Iterable[tuple[str, datetime, UUID]]) -> str:
    return sha256_hex(sorted(([ticker, ts, batch_id] for ticker, ts, batch_id in entries),
                             key=lambda entry: (entry[0], entry[1])))


def create_or_reuse_snapshot(
    conn: Connection,
    *,
    source: str,
    data_as_of: datetime,
    tickers: Sequence[str],
    range_from: datetime,
    range_to: datetime,
) -> UUID:
    ordered = sorted(set(tickers))
    entries: list[tuple[str, datetime, UUID]] = []
    for ticker in ordered:
        for item in read_bars_as_of(conn, ticker, source, range_from, range_to, data_as_of):
            if item.batch_id is None:
                raise ValueError(f"bar {item.ts.isoformat()} has no batch_id")
            entries.append((ticker, item.ts, item.batch_id))
    digest = manifest_hash(entries)
    table = market_data_snapshots
    existing: UUID | None = conn.execute(
        select(table.c.id).where(
            table.c.source == source, table.c.data_as_of == data_as_of, table.c.tickers == ordered,
            table.c.range_from == range_from, table.c.range_to == range_to,
            table.c.content_manifest_hash == digest,
        ).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    snapshot_id = uuid4()
    conn.execute(table.insert().values(
        id=snapshot_id, created_at=func.clock_timestamp(), source=source, data_as_of=data_as_of,
        tickers=ordered, range_from=range_from, range_to=range_to, content_manifest_hash=digest,
    ))
    return snapshot_id
