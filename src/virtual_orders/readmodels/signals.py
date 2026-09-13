"""GET /signals read model (D19: `date` is the America/New_York date of created_at)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Connection, and_, func, select

from virtual_orders.storage.tables import order_state, orders, signals

MARKET_TIMEZONE = "America/New_York"


def list_signals(
    conn: Connection, *, day: date | None, strategy: str | None, limit: int, offset: int
) -> list[dict[str, Any]]:
    auto = orders.alias("auto_order")
    query = (
        select(
            signals.c.id.label("signal_id"), signals.c.client_signal_id, signals.c.created_at, signals.c.strategy,
            signals.c.strategy_version, signals.c.source, signals.c.ticker, signals.c.direction,
            signals.c.entry_zone_low, signals.c.entry_zone_high, signals.c.trigger_price, signals.c.stop,
            signals.c.target1, signals.c.target2, signals.c.valid_sessions, signals.c.evaluation_start_ts,
            signals.c.valid_until_ts, signals.c.score, auto.c.id.label("auto_order_id"),
            order_state.c.status.label("auto_order_status"),
        )
        .select_from(
            signals.outerjoin(auto, and_(auto.c.signal_id == signals.c.id, auto.c.origin == "AUTO_STRATEGY",
                                         auto.c.replay.is_(False)))
            .outerjoin(order_state, order_state.c.order_id == auto.c.id)
        )
    )
    if day is not None:
        query = query.where(func.date(func.timezone(MARKET_TIMEZONE, signals.c.created_at)) == day)
    if strategy is not None:
        query = query.where(signals.c.strategy == strategy)
    rows = conn.execute(query.order_by(signals.c.created_at.desc(), signals.c.id).limit(limit).offset(offset))
    return [dict(row) for row in rows.mappings()]
