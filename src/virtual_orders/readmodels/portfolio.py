"""GET /portfolio/virtual (D45): open non-replay positions marked at the last stored close as of `as_of`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Connection, text

from core.domain.models import Direction
from virtual_orders.analytics.portfolio import OpenPosition, VirtualPortfolio, mark_portfolio

_OPEN_POSITIONS = text(
    """
    SELECT o.id AS order_id, g.ticker, g.direction, g.strategy, o.origin, o.risk_amount,
           st.status, st.frozen, st.qty_open, st.avg_entry, st.stop_current, st.realized_pnl, st.costs,
           st.state_document->>'dividends' AS dividends, mark.close AS last_close, mark.ts AS last_close_ts
    FROM order_state st
    JOIN orders o ON o.id = st.order_id
    JOIN signals g ON g.id = o.signal_id
    LEFT JOIN LATERAL (
        SELECT b.ts, b.close
        FROM bars_1m b JOIN bar_batches bb ON bb.batch_id = b.batch_id
        WHERE b.ticker = g.ticker AND b.source = o.price_source AND bb.ingested_at <= :as_of
        ORDER BY b.ts DESC, bb.ingested_at DESC, b.batch_id DESC
        LIMIT 1
    ) mark ON true
    WHERE NOT o.replay AND st.qty_open > 0 AND st.avg_entry IS NOT NULL
    ORDER BY g.ticker, o.id
    """
)


def virtual_portfolio(conn: Connection, *, as_of: datetime) -> VirtualPortfolio:
    return mark_portfolio([
        OpenPosition(
            order_id=row.order_id, ticker=row.ticker, direction=Direction(row.direction), strategy=row.strategy,
            origin=row.origin, status=row.status, frozen=row.frozen, qty_open=row.qty_open, avg_entry=row.avg_entry,
            stop_current=row.stop_current, risk_amount=row.risk_amount, realized_pnl=row.realized_pnl,
            costs=row.costs, dividends=Decimal(row.dividends or "0"), last_close=row.last_close,
            last_close_ts=row.last_close_ts,
        )
        for row in conn.execute(_OPEN_POSITIONS, {"as_of": as_of})
    ])
