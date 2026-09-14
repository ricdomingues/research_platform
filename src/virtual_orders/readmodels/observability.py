"""Operator views for the Health page (D42): health transitions, alert delivery and data coverage. Reads only.

Alert documents are never returned (they can carry event payloads); error columns already hold exception types only.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, select, text

from core.domain.hashing import CANONICAL_CONTEXT
from virtual_orders.storage.tables import health_state_log

COVERAGE_WINDOW = timedelta(days=14)
PCT_QUANTUM = Decimal("0.01")


class OutboxOutcome(StrEnum):
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    PENDING = "PENDING"  # no attempt yet, or the latest attempt failed


def health_log(conn: Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(select(health_state_log).order_by(health_state_log.c.id.desc()).limit(limit))
    return [{"id": row.id, "state": row.state, "cause_codes": list(row.cause_codes), "observed_at": row.observed_at}
            for row in rows]


# Only the last delivery attempt and a lifetime FAILED count are read: never the alert `document` column, which
# can carry order/event payloads. `error_type` and `status_code` are already sanitized at write time (D24/D30):
# AlertDeliveryFailed and the generic except-clause in deliver_pending_alerts store only an exception type name.
_ALERT_OUTBOX = """
    SELECT a.id, a.alert_key, a.kind, a.subject, a.subject_ts, a.created_at,
           COALESCE(failed.count, 0) AS failures,
           last.outcome AS last_outcome, last.status_code AS last_status_code,
           last.error_type AS last_error_type, last.attempted_at AS last_attempted_at
    FROM alert_outbox a
    LEFT JOIN LATERAL (
        SELECT t.outcome, t.status_code, t.error_type, t.attempted_at
        FROM alert_delivery_attempts t
        WHERE t.alert_id = a.id
        ORDER BY t.id DESC
        LIMIT 1
    ) last ON true
    LEFT JOIN LATERAL (
        SELECT COUNT(*) AS count FROM alert_delivery_attempts t
        WHERE t.alert_id = a.id AND t.outcome = 'FAILED'
    ) failed ON true
    {outcome_filter}
    ORDER BY a.id DESC
    LIMIT :limit
"""

_PENDING_FILTER = "WHERE last.outcome IS NULL OR last.outcome = 'FAILED'"
_OUTCOME_FILTER = "WHERE last.outcome = :outcome"


def alert_outbox_view(conn: Connection, *, outcome: OutboxOutcome | None, limit: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if outcome is None:
        where = ""
    elif outcome is OutboxOutcome.PENDING:
        where = _PENDING_FILTER
    else:
        where = _OUTCOME_FILTER
        params["outcome"] = outcome.value
    query = text(_ALERT_OUTBOX.format(outcome_filter=where))
    return [
        {"id": row.id, "alert_key": row.alert_key, "kind": row.kind, "subject": row.subject,
         "subject_ts": row.subject_ts, "created_at": row.created_at, "failures": int(row.failures),
         "last_outcome": row.last_outcome, "last_status_code": row.last_status_code,
         "last_error_type": row.last_error_type, "last_attempted_at": row.last_attempted_at}
        for row in conn.execute(query, params)
    ]


_COVERAGE = text(
    """
    SELECT split_part(e.event_key, ':', 2) AS session_date, COUNT(DISTINCT e.order_id) AS orders,
           SUM((e.payload->>'expected_bars')::int) AS expected_bars,
           SUM((e.payload->>'missing_bars')::int) AS missing_bars
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    WHERE NOT o.replay AND e.type = 'DATA_QUALITY' AND e.recorded_at >= clock_timestamp() - :window
    GROUP BY 1
    ORDER BY 1 DESC
    """
)

_DATA_GAPS = text(
    """
    SELECT e.order_id, g.ticker, e.event_key, e.payload->>'gap_start_ts' AS gap_start_ts,
           (e.payload->>'minutes')::int AS minutes, e.recorded_at
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND e.type = 'DATA_GAP' AND e.recorded_at >= clock_timestamp() - :window
    ORDER BY e.id DESC
    LIMIT :limit
    """
)


def _coverage_pct(expected: int, missing: int) -> Decimal | None:
    if expected <= 0:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return (Decimal(expected - missing) * 100 / Decimal(expected)).quantize(PCT_QUANTUM)


def quality_overview(conn: Connection, *, limit: int) -> dict[str, Any]:
    coverage = [
        {"session_date": row.session_date, "orders": int(row.orders), "expected_bars": int(row.expected_bars),
         "missing_bars": int(row.missing_bars),
         "coverage_pct": _coverage_pct(int(row.expected_bars), int(row.missing_bars))}
        for row in conn.execute(_COVERAGE, {"window": COVERAGE_WINDOW})
    ]
    gaps = [dict(row) for row in conn.execute(_DATA_GAPS, {"window": COVERAGE_WINDOW, "limit": limit}).mappings()]
    return {"window_days": COVERAGE_WINDOW.days, "coverage": coverage, "data_gaps": gaps}
