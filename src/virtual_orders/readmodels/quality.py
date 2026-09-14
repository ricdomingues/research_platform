"""Sessions D12 left unevaluated and not yet consumed by DATA_QUALITY or a recheck (D22)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Connection, text

RECENT_END_OF_DAY_RUNS = 20

_PENDING = text(
    """
    WITH eod AS (
        SELECT r.run_id, r.started_at, latest.detail
        FROM evaluation_runs r
        JOIN LATERAL (
            SELECT status, detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
        ) latest ON true
        WHERE r.kind = 'END_OF_DAY' AND latest.status = 'COMPLETED'
        ORDER BY r.started_at DESC, r.run_id
        LIMIT :runs
    ), items AS (
        SELECT DISTINCT ON (ne.key, eod.detail->>'session_day')
               ne.key::uuid AS order_id, (eod.detail->>'session_day')::date AS session_day,
               eod.run_id AS source_run_id, ne.value AS reason
        FROM eod, jsonb_each_text(COALESCE(eod.detail->'not_evaluated', '{}'::jsonb)) AS ne(key, value)
        WHERE eod.detail->>'session_day' IS NOT NULL
        ORDER BY ne.key, eod.detail->>'session_day', eod.started_at DESC, eod.run_id
    )
    SELECT i.order_id, i.session_day, i.source_run_id, i.reason, g.ticker, o.price_source
    FROM items i
    JOIN orders o ON o.id = i.order_id
    JOIN signals g ON g.id = o.signal_id
    WHERE NOT EXISTS (
        SELECT 1 FROM order_events e
        WHERE e.order_id = i.order_id AND e.event_key = 'DATA_QUALITY:' || to_char(i.session_day, 'YYYY-MM-DD')
    )
      AND NOT EXISTS (
        SELECT 1 FROM data_quality_rechecks q WHERE q.order_id = i.order_id AND q.session_date = i.session_day
    )
    ORDER BY i.session_day, g.ticker, i.order_id
    """
)


@dataclass(frozen=True)
class PendingQuality:
    order_id: UUID
    session_day: date
    source_run_id: UUID
    reason: str
    ticker: str
    price_source: str


def pending_quality_sessions(conn: Connection, *, runs: int = RECENT_END_OF_DAY_RUNS) -> list[PendingQuality]:
    return [
        PendingQuality(row.order_id, row.session_day, row.source_run_id, row.reason, row.ticker, row.price_source)
        for row in conn.execute(_PENDING, {"runs": runs})
    ]
