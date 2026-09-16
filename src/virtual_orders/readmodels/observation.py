"""The daily paper-observation report (Plan 4, D60-D69): one NYSE session, or a range of sessions, from stored data
only and as of the request. Nothing here writes, calls a provider or feeds any evaluation."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, text

from virtual_orders.analytics.observation import LatencyStats, TradeStats, latency_stats, trade_stats
from virtual_orders.readmodels.observation_operations import (
    RUN_KIND_ORDER,
    ActionabilitySection,
    AlertDeliverySection,
    DataQualitySection,
    HealthSection,
    ProviderFailures,
    RecheckSection,
    WorkerSection,
    actionability,
    alert_deliveries,
    data_quality,
    health_transitions,
    provider_failures,
    recheck_activity,
    window_runs,
    worker_activity,
)
from virtual_orders.readmodels.observation_trades import (
    LatencySection,
    PressureResultSection,
    TradesSection,
    pressure_section,
    trade_sections,
)
from virtual_orders.readmodels.observation_window import ObservationWindow

REPORT_VERSION = "OBSERVATION_REPORT_V1"
SNAPSHOT_REQUIRED = "the observation report reads one read-only REPEATABLE READ snapshot (D61)"
DEFINITIONS: dict[str, str] = {
    "window": (
        "From 00:00 ET of the day after the previous NYSE session to 00:00 ET of the day after the session. "
        "Evaluation facts belong to the window by their market instant (a still-running run without one falls back "
        "to its database start time); health log, alert and worker rows by the database clock. Every row is read "
        "as of the database clock at the request, and the whole report is read in one read-only REPEATABLE READ "
        "snapshot taken after that instant; complete = as_of is past the window end."
    ),
    "provider_failures": (
        "Provider failures recorded in run details (LIVE/WATCHLIST ingest_failures, OPENING source_failures, "
        "END_OF_DAY unavailable, QUALITY_RECHECK ingest_failures and unavailable, ACTIONABILITY ingest_error), "
        "counted per run and feed as fixed codes; messages never leave the database. Failed runs are counted by "
        "exception type name, or UNKNOWN. consecutive_live_max is the longest run of consecutive completed LIVE "
        "cycles inside this window with the same feed failing: it restarts at the window boundary, skips failed "
        "runs and is not the /health rule."
    ),
    "data_quality": (
        "Missing bars from the DATA_QUALITY:<session> events of non-replay orders (order-minutes), DATA_GAP events "
        "that start inside the session, and not_evaluated reasons of the latest completed END_OF_DAY run of the "
        "session."
    ),
    "actionability": (
        "Manual-order actionability checks clicked inside the window; unverifiable are the HTTP 503 "
        "ACTIONABILITY_UNVERIFIABLE answers, split into provider failure, missing minutes and policy contract "
        "violation."
    ),
    "rechecks": (
        "DATA_QUALITY_RECHECK runs whose market instant falls in the window and the rows they recorded, plus every "
        "recheck row about this session recorded up to the report (about_this_session). Those rows also count in "
        "the rows of the window whose run recorded them, so the two are never summed. not_evaluated excludes the "
        "ALREADY_RECHECKED and ALREADY_EVALUATED skips, which are counted in skipped."
    ),
    "alerts": (
        "Alerts created and delivery attempts made inside the window (database clock): outcomes, failure types (HTTP "
        "status or exception type) and alerts created in the window still undelivered at its end."
    ),
    "worker": (
        "worker_sessions rows: starts, restarts (a start after any earlier session), stop reasons and exit codes. A "
        "session without a stop row is open: running, or ended without one. open_session_at_end = the last session "
        "started by the window end has no stop row by then. An unclean end (no stop row before the next start) is "
        "only detectable at the next start and belongs to the window of that start; a still-open session with no "
        "later start is open, never a restart. A LOCK_LOST stop is always clean, even when recorded after the "
        "successor's start."
    ),
    "health": (
        "health_state_log rows inside the window: transitions are state changes (a row whose state differs from the "
        "previous row's, the first compared with the state in force at the window start); log_rows counts every row, "
        "including cause-code-only changes. Also states entered, cause codes and seconds spent in each state."
    ),
    "trades": (
        "Non-replay virtual orders: created, filled (FILLED bar in the window), closed (closing event recorded as of "
        "the report). Statistics exclude needs_review orders (spec 5.4); MFE/MAE come from the projection (spec D3). "
        "The MFE/MAE median is the middle value, or the mean of the two middle values for an even count (latency "
        "uses nearest-rank instead). Replay orders are only counted in replay_closed."
    ),
    "latency": (
        "Per FILLED event in the window: seconds from the signal created_at to the fill bar start and to the moment "
        "the fill was recorded; nearest-rank median and p90, never interpolated. Orders created in the window without "
        "a fill as of the report are counted by outcome (EXPIRED, INVALIDATED, CANCELED or NOT_FILLED_YET); this is a "
        "snapshot as of the report, so created is not filled plus unfilled in general."
    ),
    "pressure": (
        "An OHLCV pressure estimate recomputed at report time from the last 30 stored bars strictly before each "
        "closed trade's signal minute, across sessions and as of the report (each trade records window_start, "
        "window_end and spans_sessions), grouped by alignment with the trade direction and CMF strength against the "
        "trade R. CMF 0.05/0.15 and 30 bars are labelled conventions. A bucket mean needs at least 5 trades; n is "
        "shown beside it. It is an estimate, not order flow, descriptive only, never stored and never used by any "
        "evaluation."
    ),
}


@contextmanager
def observation_snapshot(engine: Engine) -> Iterator[Connection]:
    """D61: one read-only REPEATABLE READ transaction for a whole report or summary. Open it after acquire_data_as_of:
    every row committed up to as_of is visible, and nothing committed later changes any section of the read."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True) as conn:
        with conn.begin():
            yield conn


def require_snapshot(conn: Connection) -> None:
    isolation = conn.execute(text("SHOW transaction_isolation")).scalar_one()
    read_only = conn.execute(text("SHOW transaction_read_only")).scalar_one()
    if (isolation, read_only) != ("repeatable read", "on"):
        raise ValueError(SNAPSHOT_REQUIRED)


@dataclass(frozen=True)
class ObservationReport:
    report_version: str
    session_day: date
    window_start: datetime
    window_end: datetime
    session_open_utc: datetime
    session_close_utc: datetime
    as_of: datetime
    complete: bool
    provider_failures: ProviderFailures
    data_quality: DataQualitySection
    actionability: ActionabilitySection
    rechecks: RecheckSection
    alerts: AlertDeliverySection
    worker: WorkerSection
    health: HealthSection
    trades: TradesSection
    latency: LatencySection
    pressure: PressureResultSection
    definitions: dict[str, str]


@dataclass(frozen=True)
class DailyRow:
    session_day: date
    complete: bool
    provider_failures: int
    expected_bars: int
    missing_bars: int
    actionability_unverifiable: int
    rechecks: int
    alert_failures: int
    alerts_expired: int
    worker_restarts: int
    unclean_worker_ends: int
    health_transitions: int
    fills: int
    trades: int
    sum_r: Decimal


@dataclass(frozen=True)
class ObservationSummary:
    report_version: str
    first_day: date
    last_day: date
    as_of: datetime
    sessions: int
    days: tuple[DailyRow, ...]
    trades: TradeStats
    excluded_needs_review: int
    latency: LatencyStats
    recorded_latency: LatencyStats
    pressure: PressureResultSection
    definitions: dict[str, str]


def build_observation_report(conn: Connection, window: ObservationWindow) -> ObservationReport:
    require_snapshot(conn)  # D61: every section below reads the same snapshot
    runs = window_runs(conn, window, RUN_KIND_ORDER)  # D69: one scan of evaluation_runs per report
    facts = trade_sections(conn, window)
    return ObservationReport(
        report_version=REPORT_VERSION, session_day=window.session_day, window_start=window.start,
        window_end=window.end, session_open_utc=window.session_open_utc, session_close_utc=window.session_close_utc,
        as_of=window.as_of, complete=window.complete, provider_failures=provider_failures(conn, window, runs=runs),
        data_quality=data_quality(conn, window, runs=runs), actionability=actionability(conn, window, runs=runs),
        rechecks=recheck_activity(conn, window, runs=runs), alerts=alert_deliveries(conn, window),
        worker=worker_activity(conn, window), health=health_transitions(conn, window), trades=facts.trades,
        latency=facts.latency, pressure=facts.pressure, definitions=dict(DEFINITIONS),
    )


def daily_row(report: ObservationReport) -> DailyRow:
    return DailyRow(
        session_day=report.session_day, complete=report.complete,
        provider_failures=report.provider_failures.total_failures, expected_bars=report.data_quality.expected_bars,
        missing_bars=report.data_quality.missing_bars, actionability_unverifiable=report.actionability.unverifiable,
        rechecks=report.rechecks.rows, alert_failures=report.alerts.attempts.get("FAILED", 0),
        alerts_expired=report.alerts.attempts.get("EXPIRED", 0), worker_restarts=report.worker.restarts,
        unclean_worker_ends=report.worker.unclean_ends, health_transitions=report.health.transitions,
        fills=report.trades.filled, trades=report.trades.stats.trades, sum_r=report.trades.stats.sum_r,
    )


def build_observation_summary(conn: Connection, windows: Sequence[ObservationWindow]) -> ObservationSummary:
    if not windows:
        raise ValueError("a summary needs at least one session window")
    if len({window.as_of for window in windows}) != 1:
        raise ValueError("a summary needs every window to share one as_of")
    if [window.session_day for window in windows] != sorted(window.session_day for window in windows):
        raise ValueError("a summary needs its windows sorted by session_day")
    reports = [build_observation_report(conn, window) for window in windows]
    rows = [row for report in reports for row in report.trades.rows]
    included = [row for row in rows if not row.needs_review]
    fills = [fill for report in reports for fill in report.latency.rows]
    return ObservationSummary(
        report_version=REPORT_VERSION, first_day=windows[0].session_day, last_day=windows[-1].session_day,
        as_of=windows[0].as_of, sessions=len(reports), days=tuple(daily_row(report) for report in reports),
        trades=trade_stats([(row.r_multiple, row.mfe_r, row.mae_r) for row in included]),
        excluded_needs_review=len(rows) - len(included),
        latency=latency_stats([fill.bar_latency_seconds for fill in fills]),
        recorded_latency=latency_stats([fill.recorded_latency_seconds for fill in fills]),
        pressure=pressure_section(rows), definitions=dict(DEFINITIONS),
    )
