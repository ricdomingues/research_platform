"""End-of-day observation alert (Plan 4, D70): counts and codes only, one per session. n8n is never in the critical
path (spec 1.2 item 7): the job enqueues it inside a contained block after the session's work is done."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import Engine

from virtual_orders.alerts.outbox import AlertKind, enqueue_alert
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation import ObservationReport, build_observation_report, observation_snapshot
from virtual_orders.readmodels.observation_window import session_window

OBSERVATION_ALERT_FIELDS = (
    "session_day", "as_of", "complete", "provider_failures", "provider_failure_codes", "consecutive_live_failures",
    "expected_bars", "missing_bars", "data_gaps", "actionability_requests", "actionability_unverifiable", "rechecks",
    "recheck_statuses", "alert_delivery_failures", "alerts_expired", "worker_starts", "worker_restarts",
    "unclean_worker_ends", "health_transitions", "health_state_at_end", "orders_created", "fills", "trades_closed",
    "excluded_needs_review", "sum_r", "mean_r", "median_bar_latency_seconds", "p90_bar_latency_seconds",
)


def observation_alert_document(report: ObservationReport) -> dict[str, Any]:
    codes: Counter[str] = Counter()
    for item in report.provider_failures.by_run_kind:
        codes.update(item.codes)
    document: dict[str, Any] = {
        "session_day": report.session_day, "as_of": report.as_of, "complete": report.complete,
        "provider_failures": report.provider_failures.total_failures,
        "provider_failure_codes": dict(sorted(codes.items())),
        "consecutive_live_failures": report.provider_failures.consecutive_live_max,
        "expected_bars": report.data_quality.expected_bars, "missing_bars": report.data_quality.missing_bars,
        "data_gaps": report.data_quality.gaps, "actionability_requests": report.actionability.requests,
        "actionability_unverifiable": report.actionability.unverifiable, "rechecks": report.rechecks.rows,
        "recheck_statuses": report.rechecks.statuses,
        "alert_delivery_failures": report.alerts.attempts.get("FAILED", 0),
        "alerts_expired": report.alerts.attempts.get("EXPIRED", 0), "worker_starts": report.worker.starts,
        "worker_restarts": report.worker.restarts, "unclean_worker_ends": report.worker.unclean_ends,
        "health_transitions": report.health.transitions, "health_state_at_end": report.health.state_at_end,
        "orders_created": sum(report.trades.created.values()), "fills": report.trades.filled,
        "trades_closed": report.trades.stats.trades, "excluded_needs_review": report.trades.excluded_needs_review,
        "sum_r": report.trades.stats.sum_r, "mean_r": report.trades.stats.mean_r,
        "median_bar_latency_seconds": report.latency.bar.median_seconds,
        "p90_bar_latency_seconds": report.latency.bar.p90_seconds,
    }
    return document


def enqueue_observation_alert(engine: Engine, *, session_day: date) -> bool:
    """Idempotent by `OBSERVATION_DAILY:<session>`: the first end-of-day run of the session wins (D70).

    The report is read in one read-only REPEATABLE READ snapshot (D61); the outbox row is written afterwards in its
    own write transaction."""
    window = session_window(session_day, acquire_data_as_of(engine))
    with observation_snapshot(engine) as conn:
        document = observation_alert_document(build_observation_report(conn, window))
    with engine.begin() as conn:
        return enqueue_alert(
            conn, alert_key=f"OBSERVATION_DAILY:{session_day.isoformat()}", kind=AlertKind.OBSERVATION_DAILY,
            document=document, subject=session_day.isoformat(),
        )
