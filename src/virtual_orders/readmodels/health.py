"""Structured health (D17): facts collected from the database, then pure rules turn them into causes and a state.

Only an unreachable database or a schema off the migration head is UNHEALTHY (HTTP 503). Everything the
spec calls "degradado" (integrity incidents, provider failures for 3 consecutive cycles, failed cycles) is
DEGRADED, so /health keeps answering 200 with its causes exactly when an operator needs them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Connection, Engine, func, select, text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from virtual_orders.evaluator.manual import ACTIONABILITY_UNVERIFIABLE, actionability_outcomes
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.readmodels.incidents import (
    MAX_LISTED_ORDERS,
    ErrorCategory,
    IncidentGroup,
    classify_order_errors,
    incident_groups,
)
from virtual_orders.storage.tables import bar_batches, integrity_incidents, order_state, orders

EXPECTED_SCHEMA_REVISION = "0002"  # alembic head; pinned by test_expected_schema_revision_is_the_migration_head
CONSECUTIVE_FAILURE_THRESHOLD = 3  # spec 6: three consecutive failed cycles degrade /health
INCIDENT_WINDOW = timedelta(hours=24)
STALE_CYCLE_MULTIPLIER = 3
RECENT_LIVE_RUNS = 20
UNKNOWN_DATA_SOURCE = "UNKNOWN_DATA_SOURCE"

# D19: run details read from the database may carry free-text exception/provider messages (host names, DSNs,
# ticker-specific errors). `facts` and cause details only ever expose the keys below, never a message value.
_DETAIL_ALLOWED_KEYS = frozenset({
    "orders", "market_now", "session_day",
    "dividend_orders", "split_orders", "dividend_sources", "split_source",
    "integrity_errors", "order_errors", "not_evaluated",
    "dividend_integrity_errors", "dividend_order_errors", "split_integrity_errors", "split_order_errors",
    "ingest_failures", "unavailable", "source_failures", "error",
})
_DETAIL_FAILURE_MAP_KEYS = frozenset({"ingest_failures", "unavailable", "source_failures"})


class HealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class Severity(StrEnum):
    INFO = "INFO"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


_RANK = {Severity.UNHEALTHY: 0, Severity.DEGRADED: 1, Severity.INFO: 2}


@dataclass(frozen=True)
class Cause:
    code: str
    severity: Severity
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    kind: str
    status: str
    started_at: datetime
    data_as_of: datetime
    detail: dict[str, Any]


@dataclass(frozen=True)
class HealthSnapshot:
    now: datetime
    session_open_utc: datetime | None  # open of the session in progress at `now`; None outside sessions
    live_runs: tuple[RunSummary, ...]  # newest first
    last_end_of_day: RunSummary | None
    last_opening: RunSummary | None
    latest_ingested_at: datetime | None
    incident_groups: tuple[IncidentGroup, ...]  # recorded within INCIDENT_WINDOW
    incidents_total: int
    frozen_orders: int  # non-replay projections with frozen = true
    orders_without_projection: int  # non-replay orders with no order_state row (Plan 2 close-out ruling 10)
    orders_without_projection_ids: tuple[str, ...]  # the first MAX_LISTED_ORDERS of them
    needs_review: dict[str, int]  # reason -> non-replay orders flagged with it
    actionability: dict[str, int]  # result -> ACTIONABILITY runs within INCIDENT_WINDOW


@dataclass(frozen=True)
class HealthReport:
    state: HealthState
    causes: tuple[Cause, ...]
    snapshot: HealthSnapshot | None


def _completed(runs: tuple[RunSummary, ...]) -> list[RunSummary]:
    return [r for r in runs if r.status == "COMPLETED"]


def _consecutive_failures(runs: tuple[RunSummary, ...]) -> dict[str, int]:
    completed = _completed(runs)
    if not completed:
        return {}
    streaks: dict[str, int] = {}
    for key in completed[0].detail.get("ingest_failures", {}):
        streak = 0
        for item in completed:
            if key not in item.detail.get("ingest_failures", {}):
                break
            streak += 1
        streaks[key] = streak
    return streaks


def _market_now(run: RunSummary) -> datetime:
    """The market instant the cycle evaluated (`detail.market_now`); FAILED runs fall back to started_at."""
    raw = run.detail.get("market_now")
    return datetime.fromisoformat(raw) if isinstance(raw, str) else run.started_at


def _exception_type_from_repr(value: str) -> str:
    """`repr(exc)` -> its exception type name only, e.g. "OperationalError('...')" -> "OperationalError".

    D19: the writers (cycle.py, opening.py) store `repr(exc)`/`str(exc)` for operators reading the database
    directly; nothing here changes what they write. This is the read-side boundary that keeps the message
    text (hosts, DSNs, per-order detail) from ever reaching a client of /health.
    """
    return value.split("(", 1)[0].strip() or value


def _sanitize_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Allow-list a run's `detail` for `facts`/causes: structured keys and counts only, never free text."""
    sanitized: dict[str, Any] = {}
    for key, value in detail.items():
        if key not in _DETAIL_ALLOWED_KEYS:
            continue
        if key == "error":
            sanitized[key] = _exception_type_from_repr(value) if isinstance(value, str) else value
        elif key in _DETAIL_FAILURE_MAP_KEYS and isinstance(value, Mapping):
            sanitized[key] = sorted(value)
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_run(run: RunSummary) -> RunSummary:
    return replace(run, detail=_sanitize_detail(run.detail))


def _sanitize_snapshot(snapshot: HealthSnapshot) -> HealthSnapshot:
    return replace(
        snapshot,
        live_runs=tuple(_sanitize_run(r) for r in snapshot.live_runs),
        last_end_of_day=None if snapshot.last_end_of_day is None else _sanitize_run(snapshot.last_end_of_day),
        last_opening=None if snapshot.last_opening is None else _sanitize_run(snapshot.last_opening),
    )


def _order_errors(snapshot: HealthSnapshot) -> dict[str, Mapping[str, str]]:
    errors: dict[str, Mapping[str, str]] = {}
    completed = _completed(snapshot.live_runs)
    if completed:
        errors[completed[0].run_id] = completed[0].detail.get("order_errors", {})
    if snapshot.last_end_of_day is not None:
        errors[snapshot.last_end_of_day.run_id] = snapshot.last_end_of_day.detail.get("order_errors", {})
    if snapshot.last_opening is not None:
        opening = snapshot.last_opening
        errors[f"{opening.run_id}:dividends"] = opening.detail.get("dividend_order_errors", {})
        errors[f"{opening.run_id}:splits"] = opening.detail.get("split_order_errors", {})
    return errors


def evaluate_health(snapshot: HealthSnapshot, *, eval_interval_minutes: int) -> HealthReport:
    causes: list[Cause] = []
    latest_live = snapshot.live_runs[0] if snapshot.live_runs else None
    if latest_live is not None and latest_live.status == "FAILED":
        raw_error = latest_live.detail.get("error")
        causes.append(Cause("LAST_CYCLE_FAILED", Severity.DEGRADED, {
            "run_id": latest_live.run_id,
            "error": _exception_type_from_repr(raw_error) if isinstance(raw_error, str) else raw_error,
        }))
    completed = _completed(snapshot.live_runs)
    if completed:
        unknown = sorted(key for key, message in completed[0].detail.get("ingest_failures", {}).items()
                         if str(message).startswith(UNKNOWN_DATA_SOURCE))
        if unknown:
            causes.append(Cause(UNKNOWN_DATA_SOURCE, Severity.DEGRADED, {"feeds": unknown}))
    streaks = sorted(_consecutive_failures(snapshot.live_runs).items())
    sustained = {key: streak for key, streak in streaks if streak >= CONSECUTIVE_FAILURE_THRESHOLD}
    recent = {key: streak for key, streak in streaks if streak < CONSECUTIVE_FAILURE_THRESHOLD}
    if sustained:
        causes.append(Cause("INGEST_FAILURES_CONSECUTIVE", Severity.DEGRADED, {"feeds": sustained}))
    if recent:
        causes.append(Cause("INGEST_FAILURES", Severity.INFO,
                            {"feeds": recent, "threshold": CONSECUTIVE_FAILURE_THRESHOLD}))
    if snapshot.session_open_utc is not None:
        limit = timedelta(minutes=eval_interval_minutes * STALE_CYCLE_MULTIPLIER)
        last = None if latest_live is None else _market_now(latest_live)
        reference = snapshot.session_open_utc if last is None else max(last, snapshot.session_open_utc)
        if snapshot.now - reference > limit:
            causes.append(Cause("LIVE_CYCLE_STALE", Severity.DEGRADED,
                                {"last_market_now": last, "session_open_utc": snapshot.session_open_utc}))
    if snapshot.incident_groups:
        causes.append(Cause("INTEGRITY_INCIDENTS", Severity.DEGRADED,
                            {"groups": list(snapshot.incident_groups), "total": snapshot.incidents_total}))
    elif snapshot.incidents_total:
        causes.append(Cause("INTEGRITY_INCIDENTS_HISTORY", Severity.INFO, {"total": snapshot.incidents_total}))
    if snapshot.orders_without_projection:
        causes.append(Cause("PROJECTION_MISSING_ORDERS", Severity.DEGRADED, {
            "count": snapshot.orders_without_projection, "orders": list(snapshot.orders_without_projection_ids),
        }))
    groups = classify_order_errors(_order_errors(snapshot))
    infrastructure = [g for g in groups if g.category is ErrorCategory.INFRASTRUCTURE]
    programming = [g for g in groups if g.category is ErrorCategory.PROGRAMMING]
    if infrastructure:
        causes.append(Cause("INFRASTRUCTURE_ERRORS", Severity.DEGRADED, {"groups": infrastructure}))
    if programming:
        causes.append(Cause("ORDER_ERRORS", Severity.DEGRADED, {"groups": programming}))
    frozen_total = snapshot.frozen_orders + snapshot.orders_without_projection
    if frozen_total:
        causes.append(Cause("FROZEN_ORDERS", Severity.DEGRADED, {
            "count": frozen_total, "frozen_projections": snapshot.frozen_orders,
            "without_projection": snapshot.orders_without_projection,
        }))
    eod = snapshot.last_end_of_day
    if eod is not None and eod.status == "COMPLETED" and eod.detail.get("not_evaluated"):
        reasons = Counter(eod.detail["not_evaluated"].values())
        causes.append(Cause("QUALITY_NOT_EVALUATED", Severity.DEGRADED,
                            {"session_day": eod.detail.get("session_day"), "reasons": dict(sorted(reasons.items()))}))
    opening = snapshot.last_opening
    if opening is not None and (opening.status == "FAILED" or opening.detail.get("source_failures")):
        raw_error = opening.detail.get("error")
        source_failures = opening.detail.get("source_failures", {})
        causes.append(Cause("OPENING_SOURCE_FAILURES", Severity.DEGRADED, {
            "run_id": opening.run_id, "status": opening.status,
            "source_failures": sorted(source_failures) if isinstance(source_failures, Mapping) else source_failures,
            "error": _exception_type_from_repr(raw_error) if isinstance(raw_error, str) else raw_error,
        }))
    if snapshot.needs_review:
        causes.append(Cause("NEEDS_REVIEW_QUEUE", Severity.INFO,
                            {"count": sum(snapshot.needs_review.values()), "reasons": snapshot.needs_review}))
    unverifiable = snapshot.actionability.get(ACTIONABILITY_UNVERIFIABLE, 0)
    if unverifiable:
        causes.append(Cause(ACTIONABILITY_UNVERIFIABLE, Severity.INFO, {"count": unverifiable}))

    causes.sort(key=lambda cause: _RANK[cause.severity])
    worst = min((_RANK[c.severity] for c in causes), default=_RANK[Severity.INFO])
    state = {0: HealthState.UNHEALTHY, 1: HealthState.DEGRADED}.get(worst, HealthState.HEALTHY)
    return HealthReport(state, tuple(causes), _sanitize_snapshot(snapshot))


def database_unavailable(error: Exception) -> HealthReport:
    return HealthReport(HealthState.UNHEALTHY,
                        (Cause("DATABASE_UNAVAILABLE", Severity.UNHEALTHY, {"error": type(error).__name__}),), None)


def schema_not_at_head(found: str | None) -> HealthReport:
    return HealthReport(HealthState.UNHEALTHY, (Cause("SCHEMA_NOT_AT_HEAD", Severity.UNHEALTHY, {
        "expected": EXPECTED_SCHEMA_REVISION, "found": found,
    }),), None)


_RUNS = text(
    """
    SELECT r.run_id, r.kind, r.started_at, r.data_as_of, latest.status, latest.detail
    FROM evaluation_runs r
    JOIN LATERAL (
        SELECT status, detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
    ) latest ON true
    WHERE r.kind = :kind
    ORDER BY r.started_at DESC, r.run_id
    LIMIT :limit
    """
)

_REVIEW_REASONS = text(
    """
    SELECT split_part(reason, ':', 1) AS reason, COUNT(DISTINCT st.order_id) AS orders
    FROM order_state st JOIN orders o ON o.id = st.order_id,
         jsonb_array_elements_text(st.state_document->'review_reasons') AS reason
    WHERE st.needs_review AND NOT o.replay
    GROUP BY 1 ORDER BY 1
    """
)

_ORDERS_WITHOUT_PROJECTION = (
    select(orders.c.id)
    .select_from(orders.outerjoin(order_state, order_state.c.order_id == orders.c.id))
    .where(orders.c.replay.is_(False), order_state.c.order_id.is_(None))
    .order_by(orders.c.created_at, orders.c.id)
)


def _runs(conn: Connection, kind: str, limit: int) -> tuple[RunSummary, ...]:
    return tuple(
        RunSummary(str(row.run_id), row.kind, row.status, row.started_at, row.data_as_of, dict(row.detail))
        for row in conn.execute(_RUNS, {"kind": kind, "limit": limit})
    )


def schema_revision(conn: Connection) -> str | None:
    if conn.execute(text("SELECT to_regclass('alembic_version')::text")).scalar_one() is None:
        return None
    versions = sorted(str(v) for v in conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
    return ",".join(versions) or None


def collect_health_snapshot(conn: Connection, *, now: datetime) -> HealthSnapshot:
    since = now - INCIDENT_WINDOW
    end_of_day = _runs(conn, "END_OF_DAY", 1)
    opening = _runs(conn, "OPENING", 1)
    frozen = conn.execute(
        select(func.count()).select_from(order_state.join(orders, orders.c.id == order_state.c.order_id))
        .where(order_state.c.frozen.is_(True), orders.c.replay.is_(False))
    ).scalar_one()
    missing = [str(order_id) for order_id in conn.execute(_ORDERS_WITHOUT_PROJECTION).scalars()]
    return HealthSnapshot(
        now=now,
        session_open_utc=next(
            (s.open_utc for s in calendar_for_window(now, now).sessions if s.open_utc <= now < s.close_utc), None
        ),
        live_runs=_runs(conn, "LIVE", RECENT_LIVE_RUNS),
        last_end_of_day=end_of_day[0] if end_of_day else None,
        last_opening=opening[0] if opening else None,
        latest_ingested_at=conn.execute(select(func.max(bar_batches.c.ingested_at))).scalar_one(),
        incident_groups=tuple(incident_groups(conn, since=since)),
        incidents_total=int(conn.execute(select(func.count()).select_from(integrity_incidents)).scalar_one()),
        frozen_orders=int(frozen),
        orders_without_projection=len(missing),
        orders_without_projection_ids=tuple(missing[:MAX_LISTED_ORDERS]),
        needs_review={row.reason: int(row.orders) for row in conn.execute(_REVIEW_REASONS)},
        actionability=actionability_outcomes(conn, since=since),
    )


def build_health_report(engine: Engine, *, now: datetime, eval_interval_minutes: int) -> HealthReport:
    """Connection errors are DATABASE_UNAVAILABLE; any other exception is a defect and propagates (500)."""
    try:
        with engine.connect() as conn:
            revision = schema_revision(conn)
            if revision != EXPECTED_SCHEMA_REVISION:
                return schema_not_at_head(revision)
            snapshot = collect_health_snapshot(conn, now=now)
    except (OperationalError, InterfaceError, PoolTimeoutError) as exc:
        return database_unavailable(exc)
    return evaluate_health(snapshot, eval_interval_minutes=eval_interval_minutes)
