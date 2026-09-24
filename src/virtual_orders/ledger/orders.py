"""Rows and repository for signals, orders and the order_state projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.domain.models import Direction, FillConfig, OrderState, Origin, SignalSpec
from core.domain.position import excursion_r, r_multiple
from virtual_orders.ledger.errors import (
    PROJECTION_INTEGRITY_ERROR,
    LedgerIntegrityError,
    OrderNotFound,
    SignalNotFound,
)
from virtual_orders.storage.codec import (
    config_from_snapshot,
    config_to_snapshot,
    state_from_document,
    state_to_document,
    to_document,
)
from virtual_orders.storage.tables import order_state, orders, signals


@dataclass(frozen=True)
class SignalRow:
    id: UUID
    client_signal_id: str
    payload_hash: str
    created_at: datetime
    strategy: str
    strategy_version: str
    source: str
    spec: SignalSpec
    evaluation_start_ts: datetime
    valid_until_ts: datetime
    confirmation_note: str | None = None
    score: Decimal | None = None
    thesis: str | None = None


@dataclass(frozen=True)
class OrderRow:
    id: UUID
    signal_id: UUID
    origin: Origin
    created_at: datetime
    evaluation_start_ts: datetime
    valid_until_ts: datetime
    fill_model_version: str
    config: FillConfig
    code_version: str
    risk_amount: Decimal
    price_source: str
    replay: bool = False
    replay_mode: str | None = None
    replay_of_order_id: UUID | None = None
    market_data_snapshot_id: UUID | None = None


@dataclass(frozen=True)
class Projection:
    state: OrderState
    next_seq: int


def insert_signal(conn: Connection, row: SignalRow, raw_payload: Mapping[str, Any]) -> bool:
    spec = row.spec
    stmt = (
        pg_insert(signals)
        .values(
            id=row.id, client_signal_id=row.client_signal_id, payload_hash=row.payload_hash,
            created_at=row.created_at, strategy=row.strategy, strategy_version=row.strategy_version,
            source=row.source, ticker=spec.ticker, direction=spec.direction.value,
            entry_zone_low=spec.entry_zone_low, entry_zone_high=spec.entry_zone_high,
            trigger_price=spec.trigger_price, confirmation_note=row.confirmation_note,
            target1=spec.target1, target2=spec.target2, stop=spec.stop,
            valid_sessions=spec.valid_sessions, evaluation_start_ts=row.evaluation_start_ts,
            valid_until_ts=row.valid_until_ts, score=row.score, thesis=row.thesis,
            raw_payload=to_document(dict(raw_payload)),
        )
        .on_conflict_do_nothing(index_elements=["client_signal_id"])
        .returning(signals.c.id)
    )
    return conn.execute(stmt).first() is not None


def _signal(row: Any) -> SignalRow:
    spec = SignalSpec(
        ticker=row.ticker, direction=Direction(row.direction), entry_zone_low=row.entry_zone_low,
        entry_zone_high=row.entry_zone_high, stop=row.stop, target1=row.target1, target2=row.target2,
        trigger_price=row.trigger_price, valid_sessions=row.valid_sessions,
    )
    return SignalRow(
        id=row.id, client_signal_id=row.client_signal_id, payload_hash=row.payload_hash,
        created_at=row.created_at, strategy=row.strategy, strategy_version=row.strategy_version,
        source=row.source, spec=spec, evaluation_start_ts=row.evaluation_start_ts,
        valid_until_ts=row.valid_until_ts, confirmation_note=row.confirmation_note,
        score=row.score, thesis=row.thesis,
    )


def signal_from_row(row: Any) -> SignalRow:
    """A `SignalRow` from a `signals` row selected elsewhere: read models select many at once."""
    return _signal(row)


def get_signal(conn: Connection, signal_id: UUID) -> SignalRow:
    row = conn.execute(select(signals).where(signals.c.id == signal_id)).first()
    if row is None:
        raise SignalNotFound(str(signal_id))
    return _signal(row)


def find_signal_by_client_id(conn: Connection, client_signal_id: str) -> SignalRow | None:
    row = conn.execute(select(signals).where(signals.c.client_signal_id == client_signal_id)).first()
    return None if row is None else _signal(row)


def insert_order(conn: Connection, row: OrderRow) -> None:
    conn.execute(orders.insert().values(
        id=row.id, signal_id=row.signal_id, origin=row.origin.value, created_at=row.created_at,
        evaluation_start_ts=row.evaluation_start_ts, valid_until_ts=row.valid_until_ts,
        fill_model_version=row.fill_model_version, config_snapshot=config_to_snapshot(row.config),
        code_version=row.code_version, replay=row.replay, replay_mode=row.replay_mode,
        replay_of_order_id=row.replay_of_order_id, market_data_snapshot_id=row.market_data_snapshot_id,
        risk_amount=row.risk_amount, price_source=row.price_source,
    ))


def _order(row: Any) -> OrderRow:
    return OrderRow(
        id=row.id, signal_id=row.signal_id, origin=Origin(row.origin), created_at=row.created_at,
        evaluation_start_ts=row.evaluation_start_ts, valid_until_ts=row.valid_until_ts,
        fill_model_version=row.fill_model_version, config=config_from_snapshot(row.config_snapshot),
        code_version=row.code_version, risk_amount=row.risk_amount, price_source=row.price_source,
        replay=row.replay,
        replay_mode=row.replay_mode, replay_of_order_id=row.replay_of_order_id,
        market_data_snapshot_id=row.market_data_snapshot_id,
    )


def get_order(conn: Connection, order_id: UUID) -> OrderRow:
    row = conn.execute(select(orders).where(orders.c.id == order_id)).first()
    if row is None:
        raise OrderNotFound(str(order_id))
    return _order(row)


def lock_order(conn: Connection, order_id: UUID) -> OrderRow:
    """Every event write for an order starts here (spec 5.2)."""
    row = conn.execute(select(orders).where(orders.c.id == order_id).with_for_update()).first()
    if row is None:
        raise OrderNotFound(str(order_id))
    return _order(row)


def auto_order_id(conn: Connection, signal_id: UUID) -> UUID | None:
    value: UUID | None = conn.execute(
        select(orders.c.id).where(
            orders.c.signal_id == signal_id, orders.c.origin == Origin.AUTO_STRATEGY.value,
            orders.c.replay.is_(False),
        )
    ).scalar_one_or_none()
    return value


def load_projection(conn: Connection, order_id: UUID) -> Projection | None:
    row = conn.execute(
        select(order_state.c.state_document, order_state.c.next_seq).where(order_state.c.order_id == order_id)
    ).first()
    if row is None:
        return None
    try:
        state = state_from_document(row.state_document)
    except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
        raise LedgerIntegrityError(
            PROJECTION_INTEGRITY_ERROR, "stored projection cannot be decoded", order_id=order_id,
            detail={"error": repr(exc)},
        ) from exc
    return Projection(state, row.next_seq)


_QUALITY_TOTALS = text(
    """
    SELECT COALESCE(SUM((payload->>'expected_bars')::int), 0) AS expected,
           COALESCE(SUM((payload->>'missing_bars')::int), 0) AS missing
    FROM order_events WHERE order_id = :order_id AND type = 'DATA_QUALITY'
    """
)


def projection_row(conn: Connection, order: OrderRow, direction: Direction, projection: Projection) -> dict[str, Any]:
    state = projection.state
    mfe, mae = excursion_r(state, direction)
    totals = conn.execute(_QUALITY_TOTALS, {"order_id": order.id}).one()
    return {
        "order_id": order.id,
        "status": state.status.value,
        "zone_lost": state.zone_lost,
        "entry_eligible_from": state.entry_eligible_from,
        "trigger_hit_at": state.trigger_hit_at,
        "entry_path": None if state.entry_path is None else state.entry_path.value,
        "avg_entry": state.avg_entry,
        "initial_stop": state.initial_stop,
        "stop_current": state.stop_current,
        "stop_active_from": state.stop_active_from,
        "qty_total": state.qty_total,
        "qty_open": state.qty_open,
        "realized_pnl": state.realized_pnl,
        "costs": state.costs,
        "r_multiple": r_multiple(state, order.risk_amount),
        "mfe_r": mfe,
        "mae_r": mae,
        "opened_at": state.opened_at,
        "closed_at": state.closed_at,
        "final_event_ts": state.final_event_ts,
        "last_bar_ts": state.last_bar_ts,
        "expected_bars": int(totals.expected),
        "missing_bars": int(totals.missing),
        "needs_review": state.needs_review,
        "frozen": state.frozen,
        "state_document": state_to_document(state),
        "next_seq": projection.next_seq,
    }


def save_projection(conn: Connection, order: OrderRow, direction: Direction, projection: Projection) -> None:
    row = projection_row(conn, order, direction, projection)
    stmt = pg_insert(order_state).values(**row, updated_at=func.clock_timestamp())
    stmt = stmt.on_conflict_do_update(
        index_elements=["order_id"],
        set_={**{key: stmt.excluded[key] for key in row if key != "order_id"}, "updated_at": func.clock_timestamp()},
    )
    conn.execute(stmt)


def read_projection_row(conn: Connection, order_id: UUID) -> dict[str, Any] | None:
    row = conn.execute(select(order_state).where(order_state.c.order_id == order_id)).mappings().first()
    if row is None:
        return None
    return {key: value for key, value in row.items() if key != "updated_at"}


def delete_projection(conn: Connection, order_id: UUID) -> None:
    conn.execute(delete(order_state).where(order_state.c.order_id == order_id))
