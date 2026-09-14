"""Alert outbox (D24, D33, D35): append-only and idempotent by alert_key. Delivery is bounded by a time budget,
backed off per alert and expired by age; n8n is never in the critical path (spec 1.2 item 7)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from virtual_orders.alerts.sink import AlertDeliveryFailed, AlertSink
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import alert_delivery_attempts, alert_event_marks, alert_outbox

logger = logging.getLogger("virtual_orders.alerts")

ALERT_SCHEMA_VERSION = 1
DELIVERY_BATCH = 50
DELIVERY_TIME_BUDGET_SECONDS = 20.0  # checked before each attempt: a run lasts at most this plus one attempt
BACKOFF_CAP_MINUTES = 30
ALERT_EXPIRY = timedelta(hours=24)
UNDELIVERABLE_WINDOW = timedelta(days=7)
ORDER_EVENT_LOOKBACK = timedelta(days=4)  # a long weekend without re-sending the whole history on first start
ORDER_EVENT_SCAN_LIMIT = 500
ORDER_EVENT_ALERT_TYPES = (
    "FILLED", "TARGET1_HIT", "TARGET2_HIT", "STOPPED", "TIME_EXIT", "EXPIRED", "INVALIDATED", "NEEDS_REVIEW",
)
_MARKET_TZ = ZoneInfo("America/New_York")


class AlertKind(StrEnum):
    ORDER_EVENT = "ORDER_EVENT"
    ORDER_REVIEW = "ORDER_REVIEW"
    INTEGRITY_INCIDENT = "INTEGRITY_INCIDENT"
    PRICE_CROSS = "PRICE_CROSS"
    PRESSURE = "PRESSURE"
    HEALTH = "HEALTH"
    END_OF_DAY_SUMMARY = "END_OF_DAY_SUMMARY"


@dataclass(frozen=True)
class DeliveryReport:
    delivered: int
    failed: int
    expired: int = 0
    budget_exhausted: bool = False


def alert_envelope(*, alert_key: str, kind: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """D24 + T12: every alert document carries `schema_version`/`alert_key`/`kind`, merged LAST so a caller's
    document can never overwrite them (used by the outbox, the direct DB-down health alert and WORKER_LOCK_LOST)."""
    return {**dict(document), "schema_version": ALERT_SCHEMA_VERSION, "alert_key": alert_key, "kind": kind}


def enqueue_alert(
    conn: Connection,
    *,
    alert_key: str,
    kind: AlertKind,
    document: Mapping[str, Any],
    subject: str | None = None,
    subject_ts: datetime | None = None,
) -> bool:
    """True when the alert is new. The same alert_key never produces a second row (idempotent across restarts)."""
    body = to_document(alert_envelope(alert_key=alert_key, kind=kind.value, document=document))
    stmt = (
        pg_insert(alert_outbox)
        .values(alert_key=alert_key, kind=kind.value, subject=subject, subject_ts=subject_ts, document=body)
        .on_conflict_do_nothing(index_elements=["alert_key"])
        .returning(alert_outbox.c.id)
    )
    return conn.execute(stmt).first() is not None


def last_alert_ts(conn: Connection, kind: AlertKind, subject: str) -> datetime | None:
    value: datetime | None = conn.execute(
        select(func.max(alert_outbox.c.subject_ts))
        .where(alert_outbox.c.kind == kind.value, alert_outbox.c.subject == subject)
    ).scalar_one()
    return value


def backoff(failures: int) -> timedelta:
    """D24: wait after the latest failed attempt: 1, 2, 4, 8, 16, then 30 minutes."""
    if failures <= 0:
        return timedelta(0)
    return timedelta(minutes=min(2 ** min(failures - 1, 10), BACKOFF_CAP_MINUTES))


def review_session_day(ref: str, recorded_at: datetime) -> date:
    """D33: the session a NEEDS_REVIEW flag is about.

    An ISO date ref is that date; a timezone-aware ISO instant ref is its ET date; anything else falls back to the ET
    date the flag was recorded.
    """
    try:
        if len(ref) == 10:
            return date.fromisoformat(ref)
        moment = datetime.fromisoformat(ref)
    except ValueError:
        return recorded_at.astimezone(_MARKET_TZ).date()
    if moment.tzinfo is None:
        return recorded_at.astimezone(_MARKET_TZ).date()
    return moment.astimezone(_MARKET_TZ).date()


_NEW_ORDER_EVENTS = text(
    """
    SELECT e.id AS event_id, e.order_id, e.seq, e.event_key, e.type, e.bar_ts, e.price, e.qty, e.payload,
           e.recorded_at, o.origin, o.price_source, g.ticker, g.strategy, g.direction
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND e.type = ANY(:types) AND e.recorded_at >= :since
      AND NOT EXISTS (SELECT 1 FROM alert_event_marks m WHERE m.order_event_id = e.id)
    ORDER BY e.id
    LIMIT :limit
    """
)


def _order_event_alert(row: Any) -> tuple[str, AlertKind, dict[str, Any]]:
    if row.type == "NEEDS_REVIEW":  # D33: one alert per (order, reason, session)
        reason = str(row.payload.get("reason", "UNSPECIFIED"))
        ref = str(row.payload.get("ref", ""))
        session_day = review_session_day(ref, row.recorded_at)
        return (f"ORDER_REVIEW:{row.order_id}:{reason}:{session_day.isoformat()}", AlertKind.ORDER_REVIEW,
                {"review": {"reason": reason, "session_day": session_day, "first_ref": ref}})
    return f"ORDER_EVENT:{row.order_id}:{row.event_key}", AlertKind.ORDER_EVENT, {}


def enqueue_order_event_alerts(engine: Engine, *, limit: int = ORDER_EVENT_SCAN_LIMIT) -> int:
    """Marks every scanned alertable event exactly once (alert_event_marks), so collapsed reviews are never rescanned."""
    created = 0
    with engine.begin() as conn:
        now: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        rows = conn.execute(_NEW_ORDER_EVENTS, {
            "types": list(ORDER_EVENT_ALERT_TYPES), "since": now - ORDER_EVENT_LOOKBACK, "limit": limit,
        }).all()
        for row in rows:
            alert_key, kind, extra = _order_event_alert(row)
            document = {
                "order_id": row.order_id, "ticker": row.ticker, "strategy": row.strategy, "direction": row.direction,
                "origin": row.origin, "price_source": row.price_source, "recorded_at": row.recorded_at,
                "event": {"seq": row.seq, "type": row.type, "event_key": row.event_key, "bar_ts": row.bar_ts,
                          "price": row.price, "qty": row.qty, "payload": row.payload},
                **extra,
            }
            if enqueue_alert(conn, alert_key=alert_key, kind=kind, document=document, subject=str(row.order_id)):
                created += 1
            conn.execute(
                pg_insert(alert_event_marks).values(order_event_id=row.event_id, alert_key=alert_key)
                .on_conflict_do_nothing(index_elements=["order_event_id"])
            )
    return created


_NEW_INCIDENTS = text(
    """
    SELECT i.id, i.kind, i.order_id, i.event_key, i.recorded_at
    FROM integrity_incidents i
    WHERE i.recorded_at >= :since
      AND NOT EXISTS (SELECT 1 FROM alert_outbox a WHERE a.alert_key = 'INTEGRITY_INCIDENT:' || i.id::text)
    ORDER BY i.id
    LIMIT :limit
    """
)


def enqueue_incident_alerts(engine: Engine, *, limit: int = ORDER_EVENT_SCAN_LIMIT) -> int:
    """Spec 3.3: every integrity incident alerts once, orderless and replay ones included. Never its free-text detail."""
    created = 0
    with engine.begin() as conn:
        now: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        for row in conn.execute(_NEW_INCIDENTS, {"since": now - ORDER_EVENT_LOOKBACK, "limit": limit}).all():
            document = {"incident_id": row.id, "incident_kind": row.kind, "order_id": row.order_id,
                        "event_key": row.event_key, "recorded_at": row.recorded_at}
            if enqueue_alert(conn, alert_key=f"INTEGRITY_INCIDENT:{row.id}", kind=AlertKind.INTEGRITY_INCIDENT,
                             document=document, subject=None if row.order_id is None else str(row.order_id)):
                created += 1
    return created


def enqueue_event_alerts(engine: Engine) -> int:
    """The D24 enqueue step: runs in deliver_alerts and right after the opening and end-of-day jobs."""
    return enqueue_order_event_alerts(engine) + enqueue_incident_alerts(engine)


_EXPIRE = text(
    """
    INSERT INTO alert_delivery_attempts (alert_id, outcome, attempted_at)
    SELECT a.id, 'EXPIRED', :now
    FROM alert_outbox a
    WHERE a.created_at < :expiry_cutoff
      AND NOT EXISTS (
          SELECT 1 FROM alert_delivery_attempts t WHERE t.alert_id = a.id AND t.outcome IN ('DELIVERED', 'EXPIRED')
      )
    RETURNING alert_id
    """
)

_CANDIDATES = text(
    """
    SELECT a.id, a.alert_key, a.document,
           COUNT(t.id) FILTER (WHERE t.outcome = 'FAILED') AS failures,
           MAX(t.attempted_at) FILTER (WHERE t.outcome = 'FAILED') AS last_failed_at
    FROM alert_outbox a
    LEFT JOIN alert_delivery_attempts t ON t.alert_id = a.id
    WHERE a.created_at >= :expiry_cutoff
    GROUP BY a.id
    HAVING COUNT(t.id) FILTER (WHERE t.outcome IN ('DELIVERED', 'EXPIRED')) = 0
    ORDER BY a.id
    """
)

_UNDELIVERABLE = text(
    """
    SELECT COUNT(DISTINCT t.alert_id) FROM alert_delivery_attempts t
    WHERE t.outcome = 'EXPIRED' AND t.attempted_at >= clock_timestamp() - :window
    """
)

_BEHIND = text(
    """
    SELECT COUNT(*)
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    WHERE NOT o.replay AND e.type = ANY(:types)
      AND e.recorded_at < clock_timestamp() - :lookback
      AND e.recorded_at >= (SELECT min(m.recorded_at) FROM alert_event_marks m)
      AND NOT EXISTS (SELECT 1 FROM alert_event_marks m WHERE m.order_event_id = e.id)
    """
)


def deliver_pending_alerts(
    engine: Engine,
    sink: AlertSink,
    *,
    limit: int = DELIVERY_BATCH,
    now: datetime | None = None,
    time_budget_seconds: float = DELIVERY_TIME_BUDGET_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> DeliveryReport:
    """D24: expire by age, then one attempt per due alert (oldest first, per-alert backoff) within a time budget.

    `now` defaults to the database clock read once per run; it is also the `attempted_at` of every row this run writes,
    so backoff compares instants from a single clock. A failure never stops the run: only the budget does.
    """
    started = monotonic()
    with engine.begin() as conn:
        instant: datetime = (
            require_aware(now, "now") if now is not None
            else conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        )
        cutoff = instant - ALERT_EXPIRY
        expired = len(conn.execute(_EXPIRE, {"now": instant, "expiry_cutoff": cutoff}).all())
        rows = conn.execute(_CANDIDATES, {"expiry_cutoff": cutoff}).all()
    due = [row for row in rows
           if row.last_failed_at is None or instant >= row.last_failed_at + backoff(int(row.failures))][:limit]
    delivered = failed = 0
    budget_exhausted = False
    for row in due:
        if monotonic() - started >= time_budget_seconds:
            budget_exhausted = True  # the rest stays due for the next run
            break
        status_code: int | None = None
        error_type: str | None = None
        try:
            sink.deliver(row.alert_key, row.document)
        except AlertDeliveryFailed as exc:
            status_code, error_type = exc.status_code, exc.error_type
        except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path (spec 1.2 item 7)
            error_type = type(exc).__name__
        outcome = "DELIVERED" if error_type is None else "FAILED"
        with engine.begin() as conn:
            conn.execute(alert_delivery_attempts.insert().values(
                alert_id=row.id, outcome=outcome, status_code=status_code, error_type=error_type,
                attempted_at=instant,
            ))
        if outcome == "DELIVERED":
            delivered += 1
        else:
            failed += 1
            logger.warning("alert %s delivery failed via %s: %s", row.id, sink.name, error_type)
    return DeliveryReport(delivered, failed, expired, budget_exhausted)


def undeliverable_alerts(conn: Connection) -> int:
    """Alerts expired within UNDELIVERABLE_WINDOW (INFO in /health, count in the end-of-day summary)."""
    return int(conn.execute(_UNDELIVERABLE, {"window": UNDELIVERABLE_WINDOW}).scalar_one())


def order_event_alerts_behind(conn: Connection) -> int:
    """D35: unmarked alertable events that aged past the lookback after alerting started (no marks -> 0)."""
    return int(conn.execute(_BEHIND, {
        "types": list(ORDER_EVENT_ALERT_TYPES), "lookback": ORDER_EVENT_LOOKBACK,
    }).scalar_one())
