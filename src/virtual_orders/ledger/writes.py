"""Append a StepResult and refresh the projection in the caller's transaction."""

from __future__ import annotations

from sqlalchemy import Connection

from core.domain.models import Direction, StepResult
from virtual_orders.ledger.events import AppendResult, append_events
from virtual_orders.ledger.orders import OrderRow, Projection, insert_order, save_projection


def apply_result(
    conn: Connection, order: OrderRow, direction: Direction, next_seq: int, result: StepResult
) -> AppendResult:
    appended = append_events(conn, order, result.events, next_seq)
    save_projection(conn, order, direction, Projection(result.state, appended.next_seq))
    return appended


def persist_new_order(conn: Connection, order: OrderRow, direction: Direction, created: StepResult) -> AppendResult:
    insert_order(conn, order)
    return apply_result(conn, order, direction, 1, created)
