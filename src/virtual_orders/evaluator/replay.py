"""Replay (spec 3.6): REPRODUCE regenerates recorded history into a new order; nothing existing changes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, select

from virtual_orders.evaluator.history import regenerate_history
from virtual_orders.ledger.errors import REPRODUCE_DIVERGENCE, HistoryDivergence, LedgerIntegrityError, OrderNotFound
from virtual_orders.ledger.events import insert_event
from virtual_orders.ledger.orders import Projection, insert_order, lock_order, save_projection
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, record_segment, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.storage.tables import orders

ORDER_NOT_FOUND = "ORDER_NOT_FOUND"


class ReplayMode(StrEnum):
    REPRODUCE = "REPRODUCE"
    RECALCULATE = "RECALCULATE"


class ReplaySelectionError(ValueError):
    pass


@dataclass(frozen=True)
class ReplayReport:
    run_id: UUID
    mode: ReplayMode
    created: dict[UUID, UUID]
    failures: dict[UUID, str]


def select_source_orders(
    conn: Connection,
    *,
    order_ids: Sequence[UUID] | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list[UUID]:
    has_interval = created_from is not None or created_to is not None
    if order_ids is not None and has_interval:
        raise ReplaySelectionError("use order_ids or an interval, not both")
    if order_ids is not None:
        return list(dict.fromkeys(order_ids))
    if created_from is None or created_to is None:
        raise ReplaySelectionError("an interval needs both created_from and created_to")
    return list(conn.execute(
        select(orders.c.id)
        .where(orders.c.replay.is_(False), orders.c.created_at >= created_from, orders.c.created_at < created_to)
        .order_by(orders.c.created_at, orders.c.id)
    ).scalars())


def reproduce_order(engine: Engine, source_id: UUID, *, code_version: str) -> UUID:
    def operation() -> UUID:
        with engine.begin() as conn:
            source = lock_order(conn, source_id)
            try:
                history = regenerate_history(conn, source)
            except HistoryDivergence as divergence:
                raise LedgerIntegrityError(
                    REPRODUCE_DIVERGENCE, f"history cannot be reproduced: {divergence.reason}",
                    order_id=source_id, detail={"reason": divergence.reason, "diff": divergence.diff},
                ) from divergence
            replay_order = replace(
                source, id=uuid4(), code_version=code_version, replay=True,
                replay_mode=ReplayMode.REPRODUCE.value, replay_of_order_id=source.id,
            )
            insert_order(conn, replay_order)
            seq = 1
            for item in history.items:
                if item.segment is not None:
                    record_segment(conn, replace(item.segment, order_id=replay_order.id, first_seq=seq))
                for prepared in item.events:
                    insert_event(conn, replay_order.id, seq, prepared)
                    seq += 1
            save_projection(conn, replay_order, history.signal.spec.direction, Projection(history.state, seq))
            return replay_order.id

    return run_guarded(engine, operation)


def reproduce_orders(
    engine: Engine,
    *,
    code_version: str,
    order_ids: Sequence[UUID] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> ReplayReport:
    with engine.connect() as conn:
        source_ids = select_source_orders(conn, order_ids=order_ids, created_from=created_from, created_to=created_to)
    data_as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.REPLAY, data_as_of, code_version,
                        detail={"mode": ReplayMode.REPRODUCE.value, "sources": source_ids})
    created: dict[UUID, UUID] = {}
    failures: dict[UUID, str] = {}
    try:
        for source_id in source_ids:
            try:
                created[source_id] = reproduce_order(engine, source_id, code_version=code_version)
            except OrderNotFound:
                failures[source_id] = ORDER_NOT_FOUND
            except LedgerIntegrityError as error:
                failures[source_id] = error.kind
            except Exception as exc:  # noqa: BLE001 - one bad source must not abort the whole batch
                failures[source_id] = f"ERROR:{type(exc).__name__}"
        with engine.begin() as conn:
            finish_run(conn, run.run_id, RunStatus.COMPLETED, {
                "created": {str(k): v for k, v in created.items()},
                "failures": {str(k): v for k, v in failures.items()},
            })
    except Exception as exc:  # noqa: BLE001 - the run must always reach a final status
        with engine.begin() as conn:
            finish_run(conn, run.run_id, RunStatus.FAILED, {
                "error": repr(exc),
                "created": {str(k): v for k, v in created.items()},
                "failures": {str(k): v for k, v in failures.items()},
            })
        raise
    return ReplayReport(run.run_id, ReplayMode.REPRODUCE, created, failures)
