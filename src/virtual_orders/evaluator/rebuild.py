"""rebuild-projections (spec 6): order_state is a cache of the authoritative history (D2)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select

from virtual_orders.evaluator.history import regenerate_history
from virtual_orders.evaluator.outcomes import ERROR_PREFIX
from virtual_orders.ledger.errors import (
    HistoryDivergence,
    LedgerIntegrityError,
    OrderNotFound,
    ProjectionIntegrityError,
)
from virtual_orders.ledger.orders import Projection, lock_order, projection_row, read_projection_row, save_projection
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.storage.tables import orders


def rebuild_projection(engine: Engine, order_id: UUID) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        with engine.begin() as conn:
            order = lock_order(conn, order_id)
            try:
                history = regenerate_history(conn, order)
            except HistoryDivergence as divergence:
                raise ProjectionIntegrityError(
                    f"history divergence: {divergence.reason}", order_id=order_id,
                    detail={"reason": divergence.reason, "diff": divergence.diff},
                ) from divergence
            direction = history.signal.spec.direction
            rebuilt = Projection(history.state, history.next_seq)
            expected = projection_row(conn, order, direction, rebuilt)
            current = read_projection_row(conn, order_id)
            if current is None:
                save_projection(conn, order, direction, rebuilt)
                return expected
            differences = {
                key: {"stored": current[key], "rebuilt": value}
                for key, value in expected.items()
                if current[key] != value
            }
            if differences:
                raise ProjectionIntegrityError(
                    "stored projection differs from the rebuilt projection", order_id=order_id,
                    detail={"reason": "PROJECTION_MISMATCH", "differences": differences},
                )
            return expected

    return run_guarded(engine, operation)


ORDER_NOT_FOUND = "ORDER_NOT_FOUND"


def rebuild_all_projections(engine: Engine, order_ids: Sequence[UUID] | None = None) -> dict[UUID, str]:
    """rebuild-projections (spec 6): one isolated result per order; one bad order never stops the scan."""
    if order_ids is None:
        with engine.connect() as conn:
            order_ids = list(conn.execute(select(orders.c.id).order_by(orders.c.created_at, orders.c.id)).scalars())
    report: dict[UUID, str] = {}
    for order_id in order_ids:
        try:
            rebuild_projection(engine, order_id)
            report[order_id] = "OK"
        except LedgerIntegrityError as error:
            report[order_id] = error.kind
        except OrderNotFound:
            report[order_id] = ORDER_NOT_FOUND
        except Exception as exc:  # noqa: BLE001 - reported per order, like the evaluator batches
            report[order_id] = f"{ERROR_PREFIX}{type(exc).__name__}"
    return report
