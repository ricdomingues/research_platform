"""Append-only order events with strict idempotency (spec 3.3) and the 3.4 invariant."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, select

from core.domain.models import MARKET_EVENT_TYPES, Event
from virtual_orders.ledger.errors import (
    EVENT_BEFORE_EVALUATION_START,
    EVENT_HASH_CONFLICT,
    STORED_HASH_MISMATCH,
    LedgerIntegrityError,
)
from virtual_orders.ledger.orders import OrderRow
from virtual_orders.storage.codec import PreparedEvent, material_hash, prepare_event
from virtual_orders.storage.tables import order_events

MARKET_TYPE_VALUES = frozenset(t.value for t in MARKET_EVENT_TYPES)


@dataclass(frozen=True)
class StoredEvent:
    seq: int
    prepared: PreparedEvent


@dataclass(frozen=True)
class AppendResult:
    inserted: tuple[PreparedEvent, ...]
    first_seq: int
    next_seq: int


def as_prepared(item: Event | PreparedEvent) -> PreparedEvent:
    return item if isinstance(item, PreparedEvent) else prepare_event(item)


def dedupe_events(
    known: dict[str, str],
    events: Iterable[Event | PreparedEvent],
    *,
    order_id: UUID,
    evaluation_start_ts: datetime,
) -> list[PreparedEvent]:
    fresh: list[PreparedEvent] = []
    for item in events:
        prepared = as_prepared(item)
        if prepared.type in MARKET_TYPE_VALUES and (
            prepared.bar_ts is None or prepared.bar_ts < evaluation_start_ts
        ):
            raise LedgerIntegrityError(
                EVENT_BEFORE_EVALUATION_START,
                f"{prepared.event_key} bar_ts {prepared.bar_ts} precedes {evaluation_start_ts.isoformat()}",
                order_id=order_id, event_key=prepared.event_key, attempted_hash=prepared.payload_hash,
            )
        existing = known.get(prepared.event_key)
        if existing is not None:
            if existing == prepared.payload_hash:
                continue
            raise LedgerIntegrityError(
                EVENT_HASH_CONFLICT, f"{prepared.event_key} already recorded with a different payload",
                order_id=order_id, event_key=prepared.event_key, existing_hash=existing,
                attempted_hash=prepared.payload_hash,
            )
        known[prepared.event_key] = prepared.payload_hash
        fresh.append(prepared)
    return fresh


def known_hashes(conn: Connection, order_id: UUID) -> dict[str, str]:
    rows = conn.execute(
        select(order_events.c.event_key, order_events.c.payload_hash).where(order_events.c.order_id == order_id)
    ).all()
    return {row.event_key: row.payload_hash for row in rows}


def insert_event(conn: Connection, order_id: UUID, seq: int, prepared: PreparedEvent) -> None:
    conn.execute(order_events.insert().values(
        order_id=order_id, seq=seq, event_key=prepared.event_key, payload_hash=prepared.payload_hash,
        hash_material=prepared.hash_material, type=prepared.type, bar_ts=prepared.bar_ts,
        price=prepared.price, qty=prepared.qty, bar_batch_id=prepared.bar_batch_id, payload=prepared.payload,
    ))


def append_events(
    conn: Connection, order: OrderRow, events: Sequence[Event | PreparedEvent], next_seq: int
) -> AppendResult:
    fresh = dedupe_events(
        known_hashes(conn, order.id), events, order_id=order.id, evaluation_start_ts=order.evaluation_start_ts
    )
    for offset, prepared in enumerate(fresh):
        insert_event(conn, order.id, next_seq + offset, prepared)
    return AppendResult(tuple(fresh), next_seq, next_seq + len(fresh))


def stored_events(conn: Connection, order_id: UUID) -> list[StoredEvent]:
    rows = conn.execute(
        select(order_events).where(order_events.c.order_id == order_id).order_by(order_events.c.seq)
    ).all()
    result: list[StoredEvent] = []
    for row in rows:
        if material_hash(row.hash_material) != row.payload_hash:
            raise LedgerIntegrityError(
                STORED_HASH_MISMATCH, f"stored material of {row.event_key} does not match its hash",
                order_id=order_id, event_key=row.event_key, existing_hash=row.payload_hash,
            )
        result.append(StoredEvent(row.seq, PreparedEvent(
            type=row.type, event_key=row.event_key, bar_ts=row.bar_ts, price=row.price, qty=row.qty,
            bar_batch_id=row.bar_batch_id, payload=row.payload, hash_material=row.hash_material,
            payload_hash=row.payload_hash,
        )))
    return result
