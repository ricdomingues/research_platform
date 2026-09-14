"""Integrity incidents: separate transaction, incident row, order frozen with NEEDS_REVIEW:INTEGRITY (spec 3.3).

Recording the incident and freezing the order run in two separate, independently committed
transactions. Spec 3.3 requires a row in `integrity_incidents` that is never silenced: a
failure while locking, loading or freezing the order must not roll back the already-committed
incident, and must never replace the original `LedgerIntegrityError` seen by the caller.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import Connection, Engine

from core.fills import get_fill_model
from virtual_orders.ledger.errors import LedgerIntegrityError, OrderNotFound
from virtual_orders.ledger.orders import get_order, get_signal, load_projection, lock_order
from virtual_orders.ledger.writes import apply_result
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import integrity_incidents

INTEGRITY_REASON = "INTEGRITY"
QUARANTINE_FREEZE_FAILED = "QUARANTINE_FREEZE_FAILED"
T = TypeVar("T")


def record_incident(conn: Connection, error: LedgerIntegrityError) -> bool:
    """Inserts one incident row. Returns True iff `error.order_id` refers to an existing order.

    `integrity_incidents.order_id` references `orders(id)`, so an order that no longer exists
    (e.g. its own insert was rolled back by the same failure) can never take the incident down
    with it via an FK violation: when `error.order_id` does not resolve to an existing order,
    the row is stored with `order_id = NULL` and the original id is kept in
    `detail["order_id"]`.
    """
    order_id = error.order_id
    order_exists = False
    detail: dict[str, Any] = dict(error.detail)
    if order_id is not None:
        try:
            get_order(conn, order_id)
            order_exists = True
        except OrderNotFound:
            detail["order_id"] = str(order_id)
            order_id = None
    conn.execute(integrity_incidents.insert().values(
        kind=error.kind, order_id=order_id, event_key=error.event_key,
        existing_hash=error.existing_hash, attempted_hash=error.attempted_hash,
        detail=to_document({"message": str(error), **detail}),
    ))
    return order_exists


def _record_freeze_failure(engine: Engine, original: LedgerIntegrityError, freeze_error: Exception) -> None:
    """Best-effort second incident row. Never raises: a failure here must not mask `original`."""
    incident = LedgerIntegrityError(
        QUARANTINE_FREEZE_FAILED,
        f"freezing the order after {original.kind} failed: {freeze_error!r}",
        order_id=original.order_id, event_key=original.event_key,
        detail={"original_kind": original.kind, "freeze_error": repr(freeze_error)},
    )
    try:
        with engine.begin() as conn:
            record_incident(conn, incident)
    except Exception:  # noqa: BLE001 - this second incident is best-effort only
        pass


def quarantine(engine: Engine, error: LedgerIntegrityError) -> bool:
    """Returns True only when the order projection was frozen/updated.

    The incident is recorded and committed on its own first. Freezing the order then runs in a
    second, independent transaction, so any failure there (a seq conflict, a broken fill
    model, ...) can neither roll back the already-committed incident nor escape as anything
    other than `False` — callers such as `run_guarded` always see the original error. When the
    order itself does not exist there is nothing to lock or freeze, so the second transaction
    is skipped outright rather than attempted and recorded as a second failure.

    Replay orders are never frozen (D29): the incident is recorded and False is returned.
    """
    with engine.begin() as conn:
        order_exists = record_incident(conn, error)
    if error.order_id is None or not order_exists:
        return False
    try:
        with engine.begin() as conn:
            order = lock_order(conn, error.order_id)
            if order.replay:
                # D15/D29 (M5): replay orders are derived history and accept no writes; the committed
                # incident row alone records the failure.
                return False
            projection = load_projection(conn, order.id)
            if projection is None:
                return False
            signal = get_signal(conn, order.signal_id)
            frozen = get_fill_model(order.fill_model_version).freeze(projection.state, INTEGRITY_REASON, error.kind)
            apply_result(conn, order, signal.spec.direction, projection.next_seq, frozen)
            return True
    except Exception as freeze_error:  # noqa: BLE001 - never let a freeze-step failure mask the original error
        _record_freeze_failure(engine, error, freeze_error)
        return False


def run_guarded(engine: Engine, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except LedgerIntegrityError as error:
        quarantine(engine, error)
        raise
