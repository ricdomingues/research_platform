"""Non-bar commands on an order, all through the locked write path (spec 5.2)."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from types import ModuleType
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from core.domain.calendar import ONE_MINUTE
from core.domain.models import Bar, OrderContext, OrderStatus, StepResult
from core.fills import get_fill_model
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.context import load_order_context
from virtual_orders.evaluator.outcomes import OrderOutcome, isolated
from virtual_orders.ledger.errors import PROCESSED_BAR_MISSING, PROJECTION_MISSING, LedgerIntegrityError
from virtual_orders.ledger.orders import OrderRow, Projection, SignalRow, load_projection, lock_order
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import get_run, segment_containing
from virtual_orders.ledger.writes import apply_result
from virtual_orders.marketdata.asof import read_bars_as_of


class OrderAlreadyFinal(Exception):
    def __init__(self, order_id: UUID) -> None:
        super().__init__(f"order {order_id} is already final")
        self.order_id = order_id


class ReplayOrderReadOnly(Exception):
    """Replay orders are derived history (spec 3.6): no command may append to them (D15)."""

    def __init__(self, order_id: UUID) -> None:
        super().__init__(f"order {order_id} is a replay and accepts no commands")
        self.order_id = order_id


@dataclass(frozen=True)
class CommandInput:
    conn: Connection
    order: OrderRow
    signal: SignalRow
    ctx: OrderContext
    projection: Projection
    model: ModuleType


def apply_command(engine: Engine, order_id: UUID, command: Callable[[CommandInput], StepResult]) -> OrderOutcome:
    def operation() -> OrderOutcome:
        with engine.begin() as conn:
            order = lock_order(conn, order_id)
            if order.replay:
                raise ReplayOrderReadOnly(order_id)
            projection = load_projection(conn, order_id)
            if projection is None:
                raise LedgerIntegrityError(PROJECTION_MISSING, "order_state missing", order_id=order_id)
            signal, ctx = load_order_context(conn, order)
            model = get_fill_model(order.fill_model_version)
            result = command(CommandInput(conn, order, signal, ctx, projection, model))
            appended = apply_result(conn, order, signal.spec.direction, projection.next_seq, result)
            return OrderOutcome(order_id, tuple(p.event_key for p in appended.inserted))

    return run_guarded(engine, operation)


def processed_bar(conn: Connection, order: OrderRow, ticker: str, ts: datetime) -> Bar:
    segment = segment_containing(conn, order.id, ts)
    bars = []
    if segment is not None:
        run = get_run(conn, segment.run_id)
        bars = read_bars_as_of(conn, ticker, order.price_source, ts, ts + ONE_MINUTE, run.data_as_of)
    if not bars:
        raise LedgerIntegrityError(
            PROCESSED_BAR_MISSING, f"no recorded version of the bar processed at {ts.isoformat()}", order_id=order.id
        )
    return bars[0]


def cancel_order(
    engine: Engine, order_id: UUID, *, at: datetime | None = None, requested_by: str = "user"
) -> OrderOutcome:
    moment = require_aware(at, "at") if at is not None else datetime.now(UTC)

    def command(inp: CommandInput) -> StepResult:
        if inp.projection.state.is_final:
            raise OrderAlreadyFinal(order_id)
        result: StepResult = inp.model.cancel(inp.projection.state, moment, requested_by)
        return result

    return apply_command(engine, order_id, command)


def finalize_validity(engine: Engine, order_id: UUID, *, now: datetime) -> OrderOutcome:
    def command(inp: CommandInput) -> StepResult:
        state = inp.projection.state
        last_bar = None
        if state.status in (OrderStatus.OPEN, OrderStatus.PARTIAL) and state.last_bar_ts is not None:
            last_bar = processed_bar(inp.conn, inp.order, inp.signal.spec.ticker, state.last_bar_ts)
        result: StepResult = inp.model.apply_validity_end(state, inp.ctx, last_bar, now)
        return result

    return apply_command(engine, order_id, command)


def freeze_order(engine: Engine, order_id: UUID, *, reason: str, ref: str) -> OrderOutcome:
    return apply_command(engine, order_id, lambda inp: inp.model.freeze(inp.projection.state, reason, ref))


def flag_order_review(engine: Engine, order_id: UUID, *, reason: str, ref: str) -> OrderOutcome:
    return apply_command(engine, order_id, lambda inp: inp.model.flag_review(inp.projection.state, reason, ref))


_DUE_ORDERS = text(
    """
    SELECT o.id AS order_id, o.price_source AS price_source, g.ticker AS ticker
    FROM orders o LEFT JOIN order_state st ON st.order_id = o.id JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay
      AND (st.order_id IS NULL OR (NOT st.frozen AND st.status IN ('PENDING', 'OPEN', 'PARTIAL')))
      AND o.valid_until_ts <= :now
    ORDER BY o.valid_until_ts, o.id
    """
)


def expire_due_orders(engine: Engine, *, now: datetime, exclude_feeds: Collection[str] = ()) -> list[OrderOutcome]:
    """Finalizes every due order, one isolated command per order.

    `exclude_feeds` holds `"{price_source}:{ticker}"` keys whose data could not be ingested in the
    calling cycle: those orders are left for a later call instead of being finalized without data.
    Projection-less orders are selected on purpose so they surface as `PROJECTION_MISSING` incidents.
    """
    excluded = set(exclude_feeds)
    with engine.connect() as conn:
        rows = conn.execute(_DUE_ORDERS, {"now": now}).all()
    return [
        isolated(row.order_id, partial(finalize_validity, engine, row.order_id, now=now))
        for row in rows
        if f"{row.price_source}:{row.ticker}" not in excluded
    ]
