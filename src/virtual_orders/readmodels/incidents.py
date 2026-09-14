"""Read-side aggregation of integrity incidents and per-order `ERROR:<Type>` failures (D18).

`integrity_incidents` keeps one append-only row per occurrence (spec 3.3: never silenced). Repeated incidents
and catch-all errors are grouped here, for /health and operational views, never by skipping writes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, select, text

from virtual_orders.evaluator.outcomes import ERROR_PREFIX
from virtual_orders.storage.tables import integrity_incidents

MAX_LISTED_ORDERS = 100
INFRASTRUCTURE_ERROR_TYPES = frozenset({
    "OperationalError", "InterfaceError", "DisconnectionError", "TimeoutError", "ConnectionError", "OSError",
})


class ErrorCategory(StrEnum):
    INFRASTRUCTURE = "INFRASTRUCTURE"
    PROGRAMMING = "PROGRAMMING"


@dataclass(frozen=True)
class OrderErrorGroup:
    error_type: str
    category: ErrorCategory
    occurrences: int
    affected_count: int
    affected_orders: tuple[str, ...]
    runs: tuple[str, ...]


def classify_order_errors(run_errors: Mapping[str, Mapping[str, str]]) -> list[OrderErrorGroup]:
    """`run_errors`: run id -> {order id -> outcome error}. Integrity kinds (no `ERROR:` prefix) are ignored."""
    occurrences: dict[str, int] = defaultdict(int)
    orders: dict[str, set[str]] = defaultdict(set)
    runs: dict[str, set[str]] = defaultdict(set)
    for run_id, errors in run_errors.items():
        for order_id, value in errors.items():
            if not value.startswith(ERROR_PREFIX):
                continue
            error_type = value.removeprefix(ERROR_PREFIX)
            occurrences[error_type] += 1
            orders[error_type].add(order_id)
            runs[error_type].add(run_id)
    groups = [
        OrderErrorGroup(
            error_type,
            ErrorCategory.INFRASTRUCTURE if error_type in INFRASTRUCTURE_ERROR_TYPES else ErrorCategory.PROGRAMMING,
            count,
            len(orders[error_type]),
            tuple(sorted(orders[error_type]))[:MAX_LISTED_ORDERS],
            tuple(sorted(runs[error_type])),
        )
        for error_type, count in occurrences.items()
    ]
    return sorted(groups, key=lambda g: (g.category is not ErrorCategory.INFRASTRUCTURE, -g.occurrences, g.error_type))


@dataclass(frozen=True)
class IncidentGroup:
    kind: str
    reason: str | None
    occurrences: int
    affected_count: int
    affected_orders: tuple[str, ...]
    first_recorded_at: datetime
    last_recorded_at: datetime


_INCIDENT_GROUPS = text(
    """
    SELECT kind, detail->>'reason' AS reason, COUNT(*) AS occurrences,
           COUNT(DISTINCT COALESCE(order_id::text, detail->>'order_id')) AS affected_count,
           ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(order_id::text, detail->>'order_id')), NULL) AS affected_orders,
           MIN(recorded_at) AS first_recorded_at, MAX(recorded_at) AS last_recorded_at
    FROM integrity_incidents
    WHERE CAST(:since AS timestamptz) IS NULL OR recorded_at >= CAST(:since AS timestamptz)
    GROUP BY kind, detail->>'reason'
    ORDER BY MAX(recorded_at) DESC, kind
    """
)


def incident_groups(conn: Connection, *, since: datetime | None = None) -> list[IncidentGroup]:
    return [
        IncidentGroup(
            row.kind, row.reason, int(row.occurrences), int(row.affected_count),
            tuple(sorted(row.affected_orders))[:MAX_LISTED_ORDERS], row.first_recorded_at, row.last_recorded_at,
        )
        for row in conn.execute(_INCIDENT_GROUPS, {"since": since})
    ]


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: int
    kind: str
    order_id: UUID
    detail: dict[str, Any]
    recorded_at: datetime


def latest_incidents(conn: Connection, *, kind: str, order_ids: Collection[UUID]) -> dict[UUID, IncidentRecord]:
    """Newest incident of `kind` per order, e.g. the REPRODUCE_DIVERGENCE diff returned by POST /replay (D15)."""
    if not order_ids:
        return {}
    rows = conn.execute(
        select(
            integrity_incidents.c.id, integrity_incidents.c.kind, integrity_incidents.c.order_id,
            integrity_incidents.c.detail, integrity_incidents.c.recorded_at,
        )
        .where(integrity_incidents.c.kind == kind, integrity_incidents.c.order_id.in_(list(order_ids)))
        .order_by(integrity_incidents.c.recorded_at.desc(), integrity_incidents.c.id.desc())
    )
    latest: dict[UUID, IncidentRecord] = {}
    for row in rows:
        if row.order_id not in latest:
            latest[row.order_id] = IncidentRecord(int(row.id), row.kind, row.order_id, dict(row.detail), row.recorded_at)
    return latest
