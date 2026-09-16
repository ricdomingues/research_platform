"""Operational sections of the daily observation report (Plan 4, D61, D62, D67, D68). Reads only, as of the report.

Stored failure messages and exception text never leave this module: they become fixed codes or type names (D19).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from virtual_orders.analytics.observation import (
    UNKNOWN_STATE,
    StateChange,
    coverage_pct,
    failure_code,
    ratio,
    seconds_by_state,
)
from virtual_orders.readmodels.observation_window import ObservationWindow

# Mirrors virtual_orders.evaluator.manual.ACTIONABILITY_UNVERIFIABLE (same string value, pinned by a test):
# the observation read models never import the evaluation package.
ACTIONABILITY_UNVERIFIABLE = "ACTIONABILITY_UNVERIFIABLE"
MAX_LISTED_FEEDS = 50
# D67: where each run kind records its provider failures (feed key -> stored message).
FAILURE_MAPS: dict[str, tuple[str, ...]] = {
    "LIVE": ("ingest_failures",),
    "WATCHLIST": ("ingest_failures",),
    "OPENING": ("source_failures",),
    "END_OF_DAY": ("unavailable",),
    "QUALITY_RECHECK": ("ingest_failures", "unavailable"),
    "ACTIONABILITY": (),
}
RUN_KIND_ORDER = tuple(FAILURE_MAPS)
# D68: recheck skips that mean "already handled", never a data-quality problem (recheck.py `skip`).
RECHECK_SKIP_REASONS = frozenset({"ALREADY_RECHECKED", "ALREADY_EVALUATED"})
_ERROR_TYPE = re.compile(r"^[A-Z][A-Za-z0-9_]*(?=\()")  # a class name: uppercase first, no dots, then "("

_WINDOW_RUNS = text(
    """
    SELECT runs.run_id, runs.kind, runs.status, runs.detail, runs.instant
    FROM (
        SELECT r.run_id, r.kind, latest.status, latest.detail,
               (first.detail->>'session_day')::date AS session_day,
               COALESCE((latest.detail->>'market_now')::timestamptz, (first.detail->>'market_now')::timestamptz,
                        (first.detail->>'created_at')::timestamptz, r.started_at) AS instant
        FROM evaluation_runs r
        JOIN LATERAL (
            SELECT s.status, s.detail FROM evaluation_run_status s
            WHERE s.run_id = r.run_id AND s.recorded_at <= :as_of
            ORDER BY s.id DESC LIMIT 1
        ) latest ON true
        JOIN LATERAL (
            SELECT s.detail FROM evaluation_run_status s
            WHERE s.run_id = r.run_id AND s.recorded_at <= :as_of
            ORDER BY s.id LIMIT 1
        ) first ON true
        WHERE r.kind = ANY(CAST(:kinds AS text[])) AND r.started_at <= :as_of
    ) runs
    WHERE CASE WHEN runs.kind IN ('OPENING', 'END_OF_DAY') THEN runs.session_day = :session_day
               ELSE runs.instant >= :start AND runs.instant < :end END
    ORDER BY runs.instant, runs.run_id
    """
)


@dataclass(frozen=True)
class RunFacts:
    run_id: UUID
    kind: str
    status: str
    detail: dict[str, Any]
    instant: datetime


def window_runs(conn: Connection, window: ObservationWindow, kinds: Sequence[str]) -> list[RunFacts]:
    """D61: runs attributed by their market instant (or session day), with the latest status recorded as of."""
    rows = conn.execute(_WINDOW_RUNS, {
        "kinds": list(kinds), "as_of": window.as_of, "start": window.start, "end": window.end,
        "session_day": window.session_day,
    })
    return [RunFacts(row.run_id, row.kind, row.status, dict(row.detail), row.instant) for row in rows]


def _runs_of(
    conn: Connection, window: ObservationWindow, kinds: Sequence[str], runs: Sequence[RunFacts] | None
) -> list[RunFacts]:
    """D69: the report passes one scan of every run kind; a section called on its own reads its kinds."""
    found = window_runs(conn, window, kinds) if runs is None else runs
    return [run for run in found if run.kind in kinds]


def _counts(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _error_type(value: object) -> str:
    """`repr(exc)` -> the exception type name only (D19). Anything that does not start with `Type(` is UNKNOWN:
    the stored text itself never leaves this module, whatever a writer stored."""
    match = _ERROR_TYPE.match(str(value))
    return match.group(0) if match else "UNKNOWN"


# -- provider failures (D67) -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FeedFailures:
    run_kind: str
    runs: int
    failed_runs: int
    runs_with_failures: int
    failures: int
    codes: dict[str, int]
    feeds: tuple[str, ...]
    feeds_total: int
    failed_run_errors: dict[str, int]


@dataclass(frozen=True)
class ProviderFailures:
    by_run_kind: tuple[FeedFailures, ...]
    total_failures: int
    consecutive_live_max: int


def _failures(run: RunFacts) -> dict[str, str]:
    if run.kind == "ACTIONABILITY":
        raw = run.detail.get("ingest_error")
        if raw is None:
            return {}
        return {f"{run.detail.get('price_source')}:{run.detail.get('ticker')}": str(raw)}
    found: dict[str, str] = {}
    for key in FAILURE_MAPS.get(run.kind, ()):
        value = run.detail.get(key)
        if isinstance(value, Mapping):
            found.update({str(name): str(message) for name, message in value.items()})
    return found


def _longest_streak(failing_per_run: Sequence[set[str]]) -> int:
    best = 0
    streaks: dict[str, int] = {}
    for failing in failing_per_run:
        streaks = {feed: streaks.get(feed, 0) + 1 for feed in failing}
        best = max([best, *streaks.values()])
    return best


def provider_failures(
    conn: Connection, window: ObservationWindow, *, runs: Sequence[RunFacts] | None = None
) -> ProviderFailures:
    found_runs = _runs_of(conn, window, RUN_KIND_ORDER, runs)
    sections: list[FeedFailures] = []
    for kind in RUN_KIND_ORDER:
        of_kind = [run for run in found_runs if run.kind == kind]
        if not of_kind:
            continue
        found = [_failures(run) for run in of_kind]
        feeds = sorted({feed for failures in found for feed in failures})
        failed = [run for run in of_kind if run.status == "FAILED" and "error" in run.detail]
        sections.append(FeedFailures(
            run_kind=kind, runs=len(of_kind), failed_runs=len(failed),
            runs_with_failures=sum(1 for failures in found if failures),
            failures=sum(len(failures) for failures in found),
            codes=_counts(Counter(failure_code(message) for failures in found for message in failures.values())),
            feeds=tuple(feeds[:MAX_LISTED_FEEDS]), feeds_total=len(feeds),
            failed_run_errors=_counts(Counter(_error_type(run.detail["error"]) for run in failed)),
        ))
    # Descriptive: consecutive completed LIVE cycles inside this window only; not the /health rule (D67).
    live = [set(_failures(run)) for run in found_runs if run.kind == "LIVE" and run.status == "COMPLETED"]
    return ProviderFailures(tuple(sections), sum(item.failures for item in sections), _longest_streak(live))


# -- actionability (D68) -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionabilitySection:
    requests: int
    results: dict[str, int]
    unverifiable: int
    unverifiable_causes: dict[str, int]
    unverifiable_rate: Decimal | None
    unfinished: int


def _unverifiable_cause(detail: Mapping[str, Any]) -> str:
    if "ingest_error" in detail:
        return "PROVIDER_FAILURE"
    if detail.get("policy_contract_violation"):
        return "POLICY_CONTRACT_VIOLATION"
    return "MISSING_MINUTES"


def actionability(
    conn: Connection, window: ObservationWindow, *, runs: Sequence[RunFacts] | None = None
) -> ActionabilitySection:
    clicks = _runs_of(conn, window, ("ACTIONABILITY",), runs)
    finished = [run for run in clicks if run.status != "RUNNING"]
    unverifiable = [run for run in finished if run.detail.get("result") == ACTIONABILITY_UNVERIFIABLE]
    return ActionabilitySection(
        requests=len(clicks),
        results=_counts(Counter(str(run.detail.get("result")) for run in finished)),
        unverifiable=len(unverifiable),
        unverifiable_causes=_counts(Counter(_unverifiable_cause(run.detail) for run in unverifiable)),
        unverifiable_rate=ratio(len(unverifiable), len(clicks)),
        unfinished=len(clicks) - len(finished),
    )


# -- missing bars (D68) --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class DataQualitySection:
    orders_measured: int
    expected_bars: int
    missing_bars: int
    coverage_pct: Decimal | None
    orders_with_missing: int
    gaps: int
    gap_minutes: int
    not_evaluated: dict[str, int]


_SESSION_QUALITY = text(
    """
    SELECT COUNT(DISTINCT e.order_id) AS orders,
           COALESCE(SUM((e.payload->>'expected_bars')::int), 0) AS expected_bars,
           COALESCE(SUM((e.payload->>'missing_bars')::int), 0) AS missing_bars,
           COUNT(DISTINCT e.order_id) FILTER (WHERE (e.payload->>'missing_bars')::int > 0) AS orders_with_missing
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    WHERE NOT o.replay AND e.type = 'DATA_QUALITY' AND e.event_key = :event_key AND e.recorded_at <= :as_of
    """
)

_SESSION_GAPS = text(
    """
    SELECT COUNT(*) AS gaps, COALESCE(SUM((e.payload->>'minutes')::int), 0) AS minutes
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    WHERE NOT o.replay AND e.type = 'DATA_GAP' AND e.recorded_at <= :as_of
      AND (e.payload->>'gap_start_ts')::timestamptz >= :open
      AND (e.payload->>'gap_start_ts')::timestamptz < :close
    """
)


def data_quality(
    conn: Connection, window: ObservationWindow, *, runs: Sequence[RunFacts] | None = None
) -> DataQualitySection:
    quality = conn.execute(_SESSION_QUALITY, {
        "event_key": f"DATA_QUALITY:{window.session_day.isoformat()}", "as_of": window.as_of,
    }).one()
    gaps = conn.execute(_SESSION_GAPS, {
        "as_of": window.as_of, "open": window.session_open_utc, "close": window.session_close_utc,
    }).one()
    completed = [run for run in _runs_of(conn, window, ("END_OF_DAY",), runs) if run.status == "COMPLETED"]
    pending = completed[-1].detail.get("not_evaluated") if completed else None  # the latest run supersedes (D12)
    reasons = Counter(str(reason) for reason in pending.values()) if isinstance(pending, Mapping) else Counter()
    expected, missing = int(quality.expected_bars), int(quality.missing_bars)
    return DataQualitySection(
        orders_measured=int(quality.orders), expected_bars=expected, missing_bars=missing,
        coverage_pct=coverage_pct(expected, missing), orders_with_missing=int(quality.orders_with_missing),
        gaps=int(gaps.gaps), gap_minutes=int(gaps.minutes), not_evaluated=_counts(reasons),
    )


# -- DATA_QUALITY_RECHECK (D68) ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RecheckSection:
    runs: int
    rows: int
    statuses: dict[str, int]
    terminal_reasons: dict[str, int]
    sessions: tuple[date, ...]
    about_this_session: dict[str, int]
    not_evaluated: dict[str, int]
    skipped: dict[str, int]


_RECHECK_ROWS = text(
    """
    SELECT q.run_id, q.session_date, q.payload
    FROM data_quality_rechecks q
    WHERE q.recorded_at <= :as_of AND (q.run_id = ANY(CAST(:run_ids AS uuid[])) OR q.session_date = :session_day)
    """
)


def recheck_activity(
    conn: Connection, window: ObservationWindow, *, runs: Sequence[RunFacts] | None = None
) -> RecheckSection:
    recheck_runs = _runs_of(conn, window, ("QUALITY_RECHECK",), runs)
    run_ids = {run.run_id for run in recheck_runs}
    rows = conn.execute(_RECHECK_ROWS, {
        "as_of": window.as_of, "run_ids": list(run_ids), "session_day": window.session_day,
    }).all()
    mine = [row for row in rows if row.run_id in run_ids]
    not_evaluated: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    for run in recheck_runs:
        pending = run.detail.get("not_evaluated")
        if isinstance(pending, Mapping):
            for reason in (str(value) for value in pending.values()):
                (skipped if reason in RECHECK_SKIP_REASONS else not_evaluated)[reason] += 1
    return RecheckSection(
        runs=len(recheck_runs), rows=len(mine),
        statuses=_counts(Counter(str(row.payload.get("status")) for row in mine)),
        terminal_reasons=_counts(Counter(str(row.payload["terminal_reason"]) for row in mine
                                         if row.payload.get("terminal_reason") is not None)),
        sessions=tuple(sorted({row.session_date for row in mine})),
        # Rows about this session also count in `rows` of the window whose run recorded them: never sum the two.
        about_this_session=_counts(Counter(str(row.payload.get("status")) for row in rows
                                           if row.session_date == window.session_day)),
        not_evaluated=_counts(not_evaluated), skipped=_counts(skipped),
    )


# -- alert delivery (D68) ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AlertDeliverySection:
    created: dict[str, int]
    attempts: dict[str, int]
    failed_alerts: int
    failure_types: dict[str, int]
    pending_at_end: int


_ALERTS_CREATED = text(
    """
    SELECT kind, COUNT(*) AS alerts FROM alert_outbox
    WHERE created_at >= :start AND created_at < :end AND created_at <= :as_of
    GROUP BY kind
    """
)

_ATTEMPTS = text(
    """
    SELECT alert_id, outcome, status_code, error_type FROM alert_delivery_attempts
    WHERE attempted_at >= :start AND attempted_at < :end AND attempted_at <= :as_of
    """
)

_PENDING_AT_END = text(
    """
    SELECT COUNT(*) FROM alert_outbox a
    WHERE a.created_at >= :start AND a.created_at < :end AND a.created_at <= :as_of
      AND NOT EXISTS (
          SELECT 1 FROM alert_delivery_attempts t
          WHERE t.alert_id = a.id AND t.outcome IN ('DELIVERED', 'EXPIRED')
            AND t.attempted_at < :end AND t.attempted_at <= :as_of
      )
    """
)


def alert_deliveries(conn: Connection, window: ObservationWindow) -> AlertDeliverySection:
    params = {"start": window.start, "end": window.end, "as_of": window.as_of}
    attempts = conn.execute(_ATTEMPTS, params).all()
    failed = [row for row in attempts if row.outcome == "FAILED"]
    return AlertDeliverySection(
        created=_counts(Counter({row.kind: int(row.alerts) for row in conn.execute(_ALERTS_CREATED, params)})),
        attempts=_counts(Counter(row.outcome for row in attempts)),
        failed_alerts=len({row.alert_id for row in failed}),
        failure_types=_counts(Counter(
            f"HTTP_{row.status_code}" if row.status_code is not None else str(row.error_type or "UNKNOWN")
            for row in failed
        )),
        pending_at_end=int(conn.execute(_PENDING_AT_END, params).scalar_one()),
    )


# -- worker sessions (D62) -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkerSessionView:
    session_id: UUID
    started_at: datetime
    code_version: str
    stopped_at: datetime | None
    exit_code: int | None
    reason: str | None


@dataclass(frozen=True)
class WorkerSection:
    starts: int
    restarts: int
    unclean_ends: int
    stops: dict[str, int]
    exit_codes: dict[str, int]
    code_versions: tuple[str, ...]
    open_session_at_end: bool  # no stop row yet at the window end: running, or ended without a stop row
    sessions: tuple[WorkerSessionView, ...]


_STARTS = text(
    """
    SELECT s.session_id, s.recorded_at, s.code_version FROM worker_sessions s
    WHERE s.event = 'STARTED' AND s.recorded_at < :end AND s.recorded_at <= :as_of
      AND s.recorded_at >= COALESCE((
          SELECT max(p.recorded_at) FROM worker_sessions p
          WHERE p.event = 'STARTED' AND p.recorded_at < :start AND p.recorded_at <= :as_of
      ), :start)
    ORDER BY s.recorded_at, s.id
    """
)

_STOPS = text(
    """
    SELECT session_id, recorded_at, exit_code, reason FROM worker_sessions
    WHERE event = 'STOPPED' AND recorded_at <= :as_of AND session_id = ANY(CAST(:ids AS uuid[]))
    """
)

# M2: a session that started two or more starts before the window (so it never appears in `_STARTS`) can still
# write its STOPPED row inside the window -- the D62-blessed "a successor already took the lock before our
# finally" case. Selecting by the stop's own recorded_at, independent of `_STARTS`, lets `stops`/`exit_codes`
# count it too, by session_id union with the starts-keyed lookup above.
_STOPS_IN_WINDOW = text(
    """
    SELECT session_id, recorded_at, exit_code, reason FROM worker_sessions
    WHERE event = 'STOPPED' AND recorded_at >= :start AND recorded_at < :end AND recorded_at <= :as_of
    """
)


def worker_activity(conn: Connection, window: ObservationWindow) -> WorkerSection:
    """D62: a session without a stop row is open. An unclean end is only detectable when the NEXT start appears and
    belongs to the window of that start; a still-open session with no later start is open, never a restart."""
    params = {"start": window.start, "end": window.end, "as_of": window.as_of}
    starts = conn.execute(_STARTS, params).all()
    stops = {row.session_id: row for row in conn.execute(_STOPS, {
        "as_of": window.as_of, "ids": [row.session_id for row in starts]})}
    restarts = unclean = 0
    for index, row in enumerate(starts):
        if row.recorded_at < window.start or index == 0:
            continue  # the session in force at the start, or the first session ever recorded
        restarts += 1
        before = stops.get(starts[index - 1].session_id)
        if before is None:
            unclean += 1  # the previous process ended without a stop row: killed, crashed or host lost
        elif before.recorded_at > row.recorded_at and before.reason != "LOCK_LOST":
            unclean += 1  # a stop written after the next start, other than a successor taking a lost lock
    inside = [row for row in starts if row.recorded_at >= window.start]
    reported_stops = {**stops, **{row.session_id: row for row in conn.execute(_STOPS_IN_WINDOW, params)}}
    window_stops = [stop for stop in reported_stops.values() if window.start <= stop.recorded_at < window.end]
    last_stop = stops.get(starts[-1].session_id) if starts else None
    return WorkerSection(
        starts=len(inside), restarts=restarts, unclean_ends=unclean,
        stops=_counts(Counter(str(stop.reason) for stop in window_stops)),
        exit_codes=_counts(Counter(str(stop.exit_code) for stop in window_stops)),
        code_versions=tuple(sorted({row.code_version for row in inside})),
        open_session_at_end=bool(starts) and (last_stop is None or last_stop.recorded_at >= window.effective_end),
        sessions=tuple(
            WorkerSessionView(
                row.session_id, row.recorded_at, row.code_version,
                None if stops.get(row.session_id) is None else stops[row.session_id].recorded_at,
                None if stops.get(row.session_id) is None else stops[row.session_id].exit_code,
                None if stops.get(row.session_id) is None else stops[row.session_id].reason,
            )
            for row in inside
        ),
    )


# -- health transitions (D68) --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class HealthSection:
    state_at_start: str
    transitions: int  # state changes: sum(entered.values())
    log_rows: int  # health_state_log rows in the window, including cause-code-only changes
    entered: dict[str, int]
    cause_codes: dict[str, int]
    seconds_by_state: dict[str, int]
    state_at_end: str


_HEALTH_BEFORE = text(
    """
    SELECT state FROM health_state_log
    WHERE observed_at < :start AND observed_at <= :as_of
    ORDER BY observed_at DESC, id DESC LIMIT 1
    """
)

_HEALTH_ROWS = text(
    """
    SELECT state, cause_codes, observed_at FROM health_state_log
    WHERE observed_at >= :start AND observed_at < :stop
    ORDER BY observed_at, id
    """
)


def health_transitions(conn: Connection, window: ObservationWindow) -> HealthSection:
    before = conn.execute(_HEALTH_BEFORE, {"start": window.start, "as_of": window.as_of}).scalar_one_or_none()
    rows = conn.execute(_HEALTH_ROWS, {"start": window.start, "stop": window.effective_end}).all()
    initial = before or UNKNOWN_STATE
    entered: Counter[str] = Counter()
    current = initial
    for row in rows:
        if row.state != current:
            entered[row.state] += 1
        current = row.state
    return HealthSection(
        state_at_start=initial, transitions=sum(entered.values()), log_rows=len(rows), entered=_counts(entered),
        cause_codes=_counts(Counter(code for row in rows for code in row.cause_codes)),
        seconds_by_state=seconds_by_state(initial, [StateChange(row.observed_at, row.state) for row in rows],
                                          window.start, window.effective_end),
        state_at_end=current,
    )
