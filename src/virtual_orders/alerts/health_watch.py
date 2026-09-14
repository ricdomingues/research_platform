"""/health transition alerts (D25): one log row and at most one alert per signature change, never per cycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import SQLAlchemyError

from virtual_orders.alerts.outbox import AlertKind, enqueue_alert
from virtual_orders.alerts.sink import AlertSink
from virtual_orders.readmodels.health import HealthReport, HealthState, Severity, build_health_report
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import health_state_log

logger = logging.getLogger("virtual_orders.alerts")
ALERT_STATES = frozenset({HealthState.DEGRADED, HealthState.UNHEALTHY})


@dataclass(frozen=True)
class HealthSignature:
    state: HealthState
    cause_codes: tuple[str, ...]


@dataclass(frozen=True)
class HealthObservation:
    state: HealthState
    transitioned: bool
    alerted: bool


def signature(report: HealthReport) -> HealthSignature:
    codes = sorted({cause.code for cause in report.causes if cause.severity is not Severity.INFO})
    return HealthSignature(report.state, tuple(codes))


def _recovering(previous: HealthSignature | None, current: HealthSignature) -> bool:
    return current.state is HealthState.HEALTHY and previous is not None and previous.state in ALERT_STATES


def should_alert(previous: HealthSignature | None, current: HealthSignature) -> bool:
    if current.state not in ALERT_STATES:
        return _recovering(previous, current)  # D32: the owner learns the incident ended
    if previous is None or previous.state is not current.state:
        return True
    return bool(set(current.cause_codes) - set(previous.cause_codes))


def _document(report: HealthReport, previous: HealthSignature | None, now: datetime) -> dict[str, Any]:
    return {
        "state": report.state, "previous_state": None if previous is None else previous.state,
        "recovered": _recovering(previous, signature(report)),
        "causes": [{"code": cause.code, "severity": cause.severity} for cause in report.causes],
        "observed_at": now,
    }


def _latest(conn: Connection) -> HealthSignature | None:
    row = conn.execute(
        select(health_state_log.c.state, health_state_log.c.cause_codes)
        .order_by(health_state_log.c.id.desc()).limit(1)
    ).first()
    return None if row is None else HealthSignature(HealthState(row.state), tuple(row.cause_codes))


class HealthWatcher:
    def __init__(self, engine: Engine, *, eval_interval_minutes: int) -> None:
        self._engine = engine
        self._eval_interval_minutes = eval_interval_minutes
        self._memory: HealthSignature | None = None

    def observe(self, now: datetime, sink: AlertSink | None) -> HealthObservation:
        report = build_health_report(self._engine, now=now, eval_interval_minutes=self._eval_interval_minutes)
        current = signature(report)
        try:
            with self._engine.begin() as conn:
                logged = _latest(conn)
                # A signature seen only in memory (database unreachable) is the real previous state, so the
                # recovery from an outage is logged and alerted like any other transition (D25, D32).
                previous = self._memory if self._memory is not None and self._memory != logged else logged
                if previous == current and logged == current:
                    self._memory = current
                    return HealthObservation(report.state, False, False)
                log_id = conn.execute(
                    health_state_log.insert().values(state=current.state.value, cause_codes=list(current.cause_codes))
                    .returning(health_state_log.c.id)
                ).scalar_one()
                transitioned = previous != current
                alerted = sink is not None and transitioned and should_alert(previous, current) and enqueue_alert(
                    conn, alert_key=f"HEALTH:{log_id}", kind=AlertKind.HEALTH,
                    document=_document(report, previous, now), subject_ts=now,
                )
            self._memory = current
            return HealthObservation(report.state, transitioned, alerted)
        except SQLAlchemyError as exc:
            # Database down or schema off head: no log row is possible, so dedupe in memory and alert directly.
            logger.warning("health log unavailable (%s); using in-memory transition tracking", type(exc).__name__)
            previous_memory, self._memory = self._memory, current
            if previous_memory == current:
                return HealthObservation(report.state, False, False)
            if sink is None or not should_alert(previous_memory, current):
                return HealthObservation(report.state, True, False)
            try:
                sink.deliver(f"HEALTH_DIRECT:{current.state.value}:{now.isoformat()}",
                             to_document(_document(report, previous_memory, now)))
            except Exception as delivery_error:  # noqa: BLE001 - n8n is never in the critical path
                logger.warning("direct health alert failed via %s: %s", sink.name, type(delivery_error).__name__)
                return HealthObservation(report.state, True, False)
            return HealthObservation(report.state, True, True)
