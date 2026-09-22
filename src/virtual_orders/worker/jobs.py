"""Worker jobs (spec 5.3, D20, D21): plain callables over Services; the scheduler only decides when they run."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine, text

from virtual_orders.alerts.health_watch import HealthWatcher
from virtual_orders.alerts.observation import enqueue_observation_alert
from virtual_orders.alerts.outbox import deliver_pending_alerts, enqueue_event_alerts
from virtual_orders.alerts.summary import enqueue_end_of_day_summary
from virtual_orders.alerts.watch import run_watchlist_cycle
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.opening import run_opening
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.recheck import RecheckReport, run_quality_recheck
from virtual_orders.readmodels.health import build_health_report
from virtual_orders.research.service import run_research_scan
from virtual_orders.services import Services
from virtual_orders.worker.schedule import (
    DELIVER_ALERTS,
    END_OF_DAY,
    HEALTH_WATCH,
    LIVE_CYCLE,
    OPENING,
    RESEARCH_SCAN,
    WATCHLIST,
    live_session,
    session_closed_today,
    session_opening_today,
)

logger = logging.getLogger("virtual_orders.worker")

_SETTLED = text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM evaluation_runs r
        JOIN LATERAL (
            SELECT status, detail FROM evaluation_run_status s WHERE s.run_id = r.run_id ORDER BY s.id DESC LIMIT 1
        ) latest ON true
        WHERE r.kind = 'END_OF_DAY' AND latest.status = 'COMPLETED' AND latest.detail->>'session_day' = :day
          AND COALESCE(latest.detail->'not_evaluated', '{}'::jsonb) = '{}'::jsonb
    )
    """
)


@dataclass(frozen=True)
class JobResult:
    job: str
    ran: bool
    reason: str


def end_of_day_settled(engine: Engine, session_day: date) -> bool:
    """A COMPLETED END_OF_DAY run for the session with nothing left in not_evaluated (D20, D12(b))."""
    with engine.connect() as conn:
        return bool(conn.execute(_SETTLED, {"day": session_day.isoformat()}).scalar_one())


class WorkerJobs:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._health = HealthWatcher(services.engine, eval_interval_minutes=services.eval_interval_minutes)
        self._jobs: dict[str, Callable[[], JobResult]] = {
            LIVE_CYCLE: self.live_cycle, WATCHLIST: self.watchlist, OPENING: self.opening,
            END_OF_DAY: self.end_of_day, HEALTH_WATCH: self.health_watch, DELIVER_ALERTS: self.deliver_alerts,
            RESEARCH_SCAN: self.research_scan,
        }

    def _now(self) -> datetime:
        return require_aware(self._services.clock(), "clock")  # D21: the market instant, read once per job

    def runner(self, job_id: str) -> Callable[[], JobResult]:
        job = self._jobs[job_id]

        def run() -> JobResult:
            try:
                result = job()
            except Exception as exc:  # noqa: BLE001 - a failing job never stops the scheduler (D20)
                logger.error("job %s failed: %s", job_id, type(exc).__name__, exc_info=True)
                return JobResult(job_id, False, f"ERROR:{type(exc).__name__}")
            logger.info("job %s: %s", job_id, result.reason)
            return result

        return run

    def _enqueue_event_alerts(self) -> None:
        """D24: sink-gated and contained; a failure here never undoes the job that just ran."""
        if self._services.alert_sink is None:
            return
        try:
            enqueue_event_alerts(self._services.engine)
        except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path
            logger.error("event alert enqueue failed: %s", type(exc).__name__, exc_info=True)

    def live_cycle(self) -> JobResult:
        """Order evaluation only: no watchlist ingestion and no alert work in this job (D20, D26)."""
        now = self._now()
        if live_session(now) is None:
            return JobResult(LIVE_CYCLE, False, "OUTSIDE_SESSION")
        s = self._services
        report = run_live_cycle(s.engine, s.gateway, code_version=s.code_version, market_now=now)
        if report.skipped:
            return JobResult(LIVE_CYCLE, False, "LOCK_BUSY")
        return JobResult(LIVE_CYCLE, True, "COMPLETED")

    def watchlist(self) -> JobResult:
        """D26: its own job, so provider latency on watchlist tickers never delays or skips order evaluation."""
        now = self._now()
        if live_session(now) is None:
            return JobResult(WATCHLIST, False, "OUTSIDE_SESSION")
        s = self._services
        report = run_watchlist_cycle(s.engine, s.gateway, price_source=s.price_source, code_version=s.code_version,
                                     market_now=now, alerts_enabled=s.alert_sink is not None)
        if report.run_id is None:
            return JobResult(WATCHLIST, False, "NO_WATCHLIST")
        if report.ingest_failures:  # recorded in the WATCHLIST run detail, like LIVE ingest failures
            return JobResult(WATCHLIST, True, f"COMPLETED_WITH_INGEST_FAILURES:{len(report.ingest_failures)}")
        return JobResult(WATCHLIST, True, "COMPLETED")

    def research_scan(self) -> JobResult:
        """Plan 5 (D91): candlestick research over stored bars only.

        Its own job, never folded into LIVE_CYCLE: it reads many sessions of persisted bars and must never be
        able to delay a fill. It calls no provider, and a failure is contained by `runner` like any other job.
        """
        now = self._now()
        if live_session(now) is None:
            return JobResult(RESEARCH_SCAN, False, "OUTSIDE_SESSION")
        s = self._services
        report = run_research_scan(s.engine, code_version=s.code_version, price_source=s.price_source,
                                   market_now=now)
        if report.run_id is None:
            return JobResult(RESEARCH_SCAN, False, report.skipped or "SKIPPED")
        if report.failures:  # recorded per ticker and timeframe in the run detail
            return JobResult(RESEARCH_SCAN, True, f"COMPLETED_WITH_FAILURES:{len(report.failures)}")
        return JobResult(RESEARCH_SCAN, True,
                         f"COMPLETED DETECTIONS:{report.detections} CANDIDATES:{report.candidates}")

    def opening(self) -> JobResult:
        now = self._now()
        session = session_opening_today(now)
        if session is None:
            return JobResult(OPENING, False, "NO_SESSION_OPENING")
        s = self._services
        run_opening(s.engine, session_day=session.day, primary=s.dividend_primary, secondary=s.dividend_secondary,
                    split_source=s.split_source, tolerance=s.fill_config.dividend_tolerance,
                    code_version=s.code_version, now=now)
        self._enqueue_event_alerts()  # D24: dividend/split reviews alert right after the opening
        return JobResult(OPENING, True, "COMPLETED")

    def end_of_day(self) -> JobResult:
        now = self._now()
        session = session_closed_today(now)
        if session is None:
            return JobResult(END_OF_DAY, False, "NO_CLOSED_SESSION_TODAY")
        s = self._services
        if end_of_day_settled(s.engine, session.day):
            return JobResult(END_OF_DAY, False, "ALREADY_SETTLED")
        report = run_end_of_day(s.engine, gateway=s.gateway, reference=s.reference, session_day=session.day,
                                code_version=s.code_version, market_now=now)
        if report.cycle.skipped:
            return JobResult(END_OF_DAY, False, "LOCK_BUSY")
        recheck: RecheckReport | None = None
        try:
            recheck = run_quality_recheck(s.engine, gateway=s.gateway, reference=s.reference,
                                          code_version=s.code_version, market_now=now)
        except Exception as exc:  # noqa: BLE001 - the recheck run records its own FAILED status
            logger.error("quality recheck failed: %s", type(exc).__name__, exc_info=True)
        self._enqueue_event_alerts()  # D24: EXPIRED/TIME_EXIT/close-cycle fills and quality reviews alert tonight
        if s.alert_sink is not None:
            try:
                state = build_health_report(s.engine, now=now, eval_interval_minutes=s.eval_interval_minutes).state
                enqueue_end_of_day_summary(s.engine, report, session_day=session.day, health_state=state,
                                           recheck=recheck)
            except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path
                logger.error("end-of-day summary failed: %s", type(exc).__name__, exc_info=True)
            try:
                enqueue_observation_alert(s.engine, session_day=session.day)  # D70: counts only, one per session
            except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path
                logger.error("observation alert failed: %s", type(exc).__name__)  # D70: type only, no exc_info
        return JobResult(END_OF_DAY, True, "COMPLETED")

    def health_watch(self) -> JobResult:
        observation = self._health.observe(self._now(), self._services.alert_sink)
        return JobResult(HEALTH_WATCH, True, observation.state.value)

    def deliver_alerts(self) -> JobResult:
        sink = self._services.alert_sink
        if sink is None:
            return JobResult(DELIVER_ALERTS, False, "WEBHOOK_DISABLED")
        self._enqueue_event_alerts()  # D24: every minute, so API-originated events are caught too
        report = deliver_pending_alerts(self._services.engine, sink)
        reason = f"DELIVERED:{report.delivered} FAILED:{report.failed} EXPIRED:{report.expired}"
        return JobResult(DELIVER_ALERTS, True, f"{reason} BUDGET_EXHAUSTED" if report.budget_exhausted else reason)
