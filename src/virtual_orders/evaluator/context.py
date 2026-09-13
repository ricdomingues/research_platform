"""OrderContext from persisted rows, with the calendar identity check (Plan 1 R3)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Connection, select

from core.domain.calendar import calendar_window_hash
from core.domain.models import FillConfig, OrderContext
from virtual_orders.ledger.errors import CALENDAR_MISMATCH, LedgerIntegrityError
from virtual_orders.ledger.orders import OrderRow, SignalRow, get_signal
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.storage.tables import order_events

DEFAULT_FILL_MODEL_VERSION = "v1"
SIGNAL_CALENDAR_HORIZON = timedelta(days=35)  # 20 sessions plus holidays, before padding


def build_order_context(signal: SignalRow, order: OrderRow) -> OrderContext:
    calendar = calendar_for_window(min(signal.created_at, order.created_at), order.valid_until_ts)
    return OrderContext(signal.spec, order.config, calendar, order.evaluation_start_ts, order.valid_until_ts)


def signal_context(signal: SignalRow, config: FillConfig) -> OrderContext:
    calendar = calendar_for_window(signal.created_at, signal.valid_until_ts)
    return OrderContext(signal.spec, config, calendar, signal.evaluation_start_ts, signal.valid_until_ts)


def load_order_context(conn: Connection, order: OrderRow) -> tuple[SignalRow, OrderContext]:
    signal = get_signal(conn, order.signal_id)
    ctx = build_order_context(signal, order)
    payload = conn.execute(
        select(order_events.c.payload).where(
            order_events.c.order_id == order.id, order_events.c.event_key == "ORDER_CREATED"
        )
    ).scalar_one_or_none()
    expected = calendar_window_hash(ctx.calendar, ctx.evaluation_start_ts, ctx.valid_until_ts)
    recorded = None if payload is None else payload.get("calendar_sessions_hash")
    if recorded != expected:
        raise LedgerIntegrityError(
            CALENDAR_MISMATCH, "loaded calendar differs from the one recorded at order creation",
            order_id=order.id, event_key="ORDER_CREATED", existing_hash=recorded, attempted_hash=expected,
        )
    return signal, ctx
