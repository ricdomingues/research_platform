"""End-of-day webhook summary (spec 5.3 "webhook opcional com resumo", D24). Counts and codes only."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import Engine

from virtual_orders.alerts.outbox import AlertKind, enqueue_alert, undeliverable_alerts
from virtual_orders.evaluator.outcomes import ERROR_PREFIX, OrderOutcome
from virtual_orders.evaluator.quality import EndOfDayReport
from virtual_orders.evaluator.recheck import RecheckReport
from virtual_orders.ledger.runs import latest_run_status
from virtual_orders.readmodels.health import HealthState


def _event_counts(outcomes: list[OrderOutcome]) -> dict[str, int]:
    return dict(sorted(Counter(key.split(":", 1)[0] for outcome in outcomes for key in outcome.event_keys).items()))


def _error_counts(outcomes: list[OrderOutcome]) -> dict[str, int]:
    errors = [outcome.error for outcome in outcomes if outcome.error is not None]
    return {
        "integrity_errors": sum(1 for error in errors if not error.startswith(ERROR_PREFIX)),
        "order_errors": sum(1 for error in errors if error.startswith(ERROR_PREFIX)),
    }


def enqueue_end_of_day_summary(
    engine: Engine,
    report: EndOfDayReport,
    *,
    session_day: date,
    health_state: HealthState | None,
    recheck: RecheckReport | None,
) -> bool:
    if report.cycle.skipped or report.cycle.run_id is None:
        return False
    quality_outcomes = [] if report.quality is None else list(report.quality.outcomes)
    outcomes = list(report.cycle.outcomes) + list(report.expired) + quality_outcomes
    with engine.begin() as conn:
        not_evaluated = 0
        if report.quality is not None:
            _, detail = latest_run_status(conn, report.quality.run_id)
            not_evaluated = len(detail.get("not_evaluated", {}))
        document: dict[str, Any] = {
            "session_day": session_day,
            "cycle_run_id": report.cycle.run_id,
            "quality_run_id": None if report.quality is None else report.quality.run_id,
            "cycle_orders": len(report.cycle.outcomes),
            "expired_orders": len(report.expired),
            "quality_orders": len(quality_outcomes),
            "events": _event_counts(outcomes),
            **_error_counts(outcomes),
            "ingest_failures": sorted(report.cycle.ingest_failures),
            "reference_unavailable": [] if report.quality is None else sorted(report.quality.unavailable),
            "not_evaluated": not_evaluated,
            "rechecked": 0 if recheck is None else len(recheck.rechecked),
            "recheck_not_evaluated": 0 if recheck is None else len(recheck.not_evaluated),
            "recheck_terminal": 0 if recheck is None else len(recheck.terminal),
            "health_state": health_state,
            "undeliverable_alerts": undeliverable_alerts(conn),
        }
        return enqueue_alert(
            conn, alert_key=f"END_OF_DAY_SUMMARY:{session_day.isoformat()}:{report.cycle.run_id}",
            kind=AlertKind.END_OF_DAY_SUMMARY, document=document,
        )
