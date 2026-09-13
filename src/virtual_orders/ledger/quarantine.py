"""Integrity incidents: separate transaction, incident row, order frozen with NEEDS_REVIEW:INTEGRITY (spec 3.3)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import Connection, Engine

from core.fills import get_fill_model
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.orders import get_signal, load_projection, lock_order
from virtual_orders.ledger.writes import apply_result
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import integrity_incidents

INTEGRITY_REASON = "INTEGRITY"
T = TypeVar("T")


def record_incident(conn: Connection, error: LedgerIntegrityError) -> None:
    conn.execute(integrity_incidents.insert().values(
        kind=error.kind, order_id=error.order_id, event_key=error.event_key,
        existing_hash=error.existing_hash, attempted_hash=error.attempted_hash,
        detail=to_document({"message": str(error), **error.detail}),
    ))


def quarantine(engine: Engine, error: LedgerIntegrityError) -> bool:
    """Returns True when the order projection was frozen."""
    with engine.begin() as conn:
        record_incident(conn, error)
        if error.order_id is None:
            return False
        order = lock_order(conn, error.order_id)
        projection = load_projection(conn, order.id)
        if projection is None:
            return False
        signal = get_signal(conn, order.signal_id)
        frozen = get_fill_model(order.fill_model_version).freeze(projection.state, INTEGRITY_REASON, error.kind)
        apply_result(conn, order, signal.spec.direction, projection.next_seq, frozen)
        return True


def run_guarded(engine: Engine, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except LedgerIntegrityError as error:
        quarantine(engine, error)
        raise
