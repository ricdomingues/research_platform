"""GET /orders and GET /orders/{id} read models (spec 5.1). Reads only; never quarantines or rebuilds."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Select, select

from core.domain.calendar import ONE_MINUTE
from virtual_orders.ledger.errors import OrderNotFound
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.runs import get_run, list_segments
from virtual_orders.marketdata.asof import read_bars_as_of
from virtual_orders.storage.tables import data_quality_rechecks, order_state, orders, signals

QUALITY_EVENT_TYPES = frozenset({"DATA_QUALITY", "DATA_GAP"})
ORDER_COLUMNS = (
    orders.c.id.label("order_id"), orders.c.signal_id, orders.c.origin, orders.c.created_at,
    orders.c.evaluation_start_ts, orders.c.valid_until_ts, orders.c.fill_model_version, orders.c.code_version,
    orders.c.replay, orders.c.replay_mode, orders.c.replay_of_order_id, orders.c.market_data_snapshot_id,
    orders.c.risk_amount, orders.c.price_source, signals.c.strategy, signals.c.ticker, signals.c.direction,
    order_state.c.status, order_state.c.entry_path, order_state.c.avg_entry, order_state.c.stop_current,
    order_state.c.qty_open, order_state.c.realized_pnl, order_state.c.costs, order_state.c.r_multiple,
    order_state.c.mfe_r, order_state.c.mae_r, order_state.c.opened_at, order_state.c.closed_at,
    order_state.c.last_bar_ts, order_state.c.expected_bars, order_state.c.missing_bars,
    order_state.c.needs_review, order_state.c.frozen,
)


@dataclass(frozen=True)
class OrderFilters:
    status: str | None = None
    origin: str | None = None
    strategy: str | None = None
    replay: bool = False
    needs_review: bool | None = None


def _base() -> Select[Any]:
    return select(*ORDER_COLUMNS).select_from(
        orders.join(signals, signals.c.id == orders.c.signal_id)
        .outerjoin(order_state, order_state.c.order_id == orders.c.id)
    )


def list_orders(conn: Connection, filters: OrderFilters, *, limit: int, offset: int) -> list[dict[str, Any]]:
    query = _base().where(orders.c.replay.is_(filters.replay))
    if filters.status is not None:
        query = query.where(order_state.c.status == filters.status)
    if filters.origin is not None:
        query = query.where(orders.c.origin == filters.origin)
    if filters.strategy is not None:
        query = query.where(signals.c.strategy == filters.strategy)
    if filters.needs_review is not None:
        query = query.where(order_state.c.needs_review.is_(filters.needs_review))
    rows = conn.execute(query.order_by(orders.c.created_at.desc(), orders.c.id).limit(limit).offset(offset))
    return [dict(row) for row in rows.mappings()]


def order_detail(conn: Connection, order_id: UUID) -> dict[str, Any]:
    row = conn.execute(_base().where(orders.c.id == order_id)).mappings().first()
    if row is None:
        raise OrderNotFound(str(order_id))
    state = conn.execute(
        select(order_state.c.state_document).where(order_state.c.order_id == order_id)
    ).scalar_one_or_none()
    events = [
        {"seq": item.seq, "type": item.prepared.type, "event_key": item.prepared.event_key,
         "bar_ts": item.prepared.bar_ts, "price": item.prepared.price, "qty": item.prepared.qty,
         "payload": item.prepared.payload, "payload_hash": item.prepared.payload_hash}
        for item in stored_events(conn, order_id)
    ]
    segments = list_segments(conn, order_id)
    bars: list[dict[str, Any]] = []
    if segments:
        as_of = max(get_run(conn, segment.run_id).data_as_of for segment in segments)
        end = max(segment.bar_to for segment in segments) + ONE_MINUTE
        bars = [
            {"ts": bar.ts, "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
             "volume": bar.volume, "batch_id": bar.batch_id}
            for bar in read_bars_as_of(conn, row["ticker"], row["price_source"], row["evaluation_start_ts"], end, as_of)
        ]
    rechecks = [
        {"session_date": item.session_date, "recheck_key": item.recheck_key, "run_id": item.run_id,
         "source_run_id": item.source_run_id, "data_as_of": item.data_as_of, "payload": item.payload}
        for item in conn.execute(
            select(data_quality_rechecks)
            .where(data_quality_rechecks.c.order_id == order_id)
            .order_by(data_quality_rechecks.c.session_date, data_quality_rechecks.c.id)
        )
    ]
    return {
        "order": dict(row),
        "state": state,
        "events": events,
        "segments": [asdict(segment) for segment in segments],
        "data_quality": {
            "expected_bars": row["expected_bars"] or 0,
            "missing_bars": row["missing_bars"] or 0,
            "events": [event for event in events if event["type"] in QUALITY_EVENT_TYPES],
            "rechecks": rechecks,
        },
        "bars": bars,
    }
