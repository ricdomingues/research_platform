"""DATA_QUALITY_RECHECK (spec 10 D4; D12; D22): "what do we know today" about a session D12 skipped.

Never writes DATA_QUALITY or DATA_GAP (D4 keeps the original snapshot immutable); records its own append-only
row keyed DATA_QUALITY_RECHECK:{session_date}:{data_as_of} and flags reviews through the locked command path.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, select

from core.dataquality import (
    ReviewFlag,
    daily_range_mismatch,
    find_gaps,
    missing_bar_reviews,
    quality_window,
    session_quality,
)
from core.domain.calendar import Session
from core.domain.models import Bar, OrderState, StepResult
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.commands import CommandInput, apply_command
from virtual_orders.evaluator.context import DEFAULT_FILL_MODEL_VERSION
from virtual_orders.evaluator.outcomes import OrderOutcome, isolated, split_errors
from virtual_orders.evaluator.quality import session_for_day
from virtual_orders.ledger.events import known_hashes, stored_events
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of, bars_in_minutes, read_bars_as_of
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.sources import ReferenceSource, SourceDataError, SourceUnavailable
from virtual_orders.readmodels.quality import PendingQuality, pending_quality_sessions
from virtual_orders.storage.codec import PreparedEvent, parse_ts, to_document
from virtual_orders.storage.tables import data_quality_rechecks

RECHECK_EVALUATED = "EVALUATED"
RECHECK_NOT_MEASURABLE = "NOT_MEASURABLE"
RECHECK_NO_OBSERVATIONS_FINAL = "NO_OBSERVATIONS_FINAL"
RECHECK_PROVIDER_FAILURE_FINAL = "PROVIDER_FAILURE_FINAL"  # D52: a feed still failing after the final lookback
RECHECK_UNVERIFIED_REVIEW = "DATA_QUALITY_UNVERIFIED"  # D52 + spec 6: unverifiable minutes -> NEEDS_REVIEW with reason
RECHECK_FINAL_AFTER_SESSIONS = 5  # D22: a session still unobserved this many closed sessions later is final
# Fill models whose only stop-moving event is TARGET1_HIT (payload new_stop_level + stop_active_from), so the stop
# in force during a past session is exactly derivable from the ledger. A new model must be added consciously.
LEVEL_DERIVATION_MODELS = frozenset({DEFAULT_FILL_MODEL_VERSION})
LEVELS_FROM_LEDGER = "LEDGER_AT_SESSION_CLOSE"
LEVELS_NOT_DERIVABLE = "SKIPPED:LEVELS_NOT_DERIVABLE"
MinuteReference = dict[datetime, Bar] | None
DailyReference = tuple[Decimal, Decimal] | None


@dataclass(frozen=True)
class RecheckReport:
    run_id: UUID | None
    rechecked: dict[str, str]
    not_evaluated: dict[str, str]
    outcomes: tuple[OrderOutcome, ...] = ()
    terminal: dict[str, str] = field(default_factory=dict)  # subset of `rechecked`: terminal reason (D22)


def recheck_key(session_day: date, data_as_of: datetime) -> str:
    return f"DATA_QUALITY_RECHECK:{session_day.isoformat()}:{data_as_of.astimezone(UTC).isoformat()}"


def last_closed_session_day(now: datetime) -> date | None:
    closed = [s.day for s in calendar_for_window(now, now).sessions if s.close_utc <= now]
    return closed[-1] if closed else None


def sessions_closed_after(day: date, now: datetime) -> int:
    session = session_for_day(day)
    if session.close_utc >= now:
        return 0
    return sum(1 for s in calendar_for_window(session.close_utc, now).sessions if s.day > day and s.close_utc <= now)


def levels_state_at_close(
    state: OrderState,
    signal_stop: Decimal,
    events: Sequence[PreparedEvent],
    fill_model_version: str,
    close_utc: datetime,
) -> OrderState | None:
    """Spec 4.6 / D22: `state` with the stop fields in force up to `close_utc`, rebuilt from the ledger.

    Only the stop fields are replaced (they are all `active_levels` reads besides the signal's static levels). Returns
    None when that cannot be derived exactly; the caller then skips the level-touch check and never falls back to the
    projection's current levels.
    """
    if fill_model_version not in LEVEL_DERIVATION_MODELS:
        return None
    moves = [
        (parse_ts(event.payload["stop_active_from"]), Decimal(event.payload["new_stop_level"]))
        for event in events
        if event.type == "TARGET1_HIT" and event.bar_ts is not None and event.bar_ts <= close_utc
        and "new_stop_level" in event.payload
    ]
    if len(moves) > 1:
        return None
    if not moves:
        return replace(state, stop_current=signal_stop, stop_previous=None, stop_active_from=None)
    active_from, level = moves[0]
    return replace(state, stop_current=level, stop_previous=signal_stop, stop_active_from=active_from)


def _item_key(item: PendingQuality) -> str:
    return f"{item.order_id}:{item.session_day.isoformat()}"


def _already_rechecked(conn: Connection, order_id: UUID, session_day: date) -> bool:
    return conn.execute(
        select(data_quality_rechecks.c.id)
        .where(data_quality_rechecks.c.order_id == order_id, data_quality_rechecks.c.session_date == session_day)
        .limit(1)
    ).first() is not None


def _recheck_command(
    run: RunInfo,
    session: Session,
    item: PendingQuality,
    minute_reference: MinuteReference,
    daily_reference: DailyReference,
    feed_failed: bool,
    past_final_lookback: bool,
    rechecked: dict[str, str],
    not_evaluated: dict[str, str],
    terminal: dict[str, str],
) -> Callable[[CommandInput], StepResult]:
    day = session.day.isoformat()
    key = _item_key(item)
    identity = recheck_key(session.day, run.data_as_of)

    def skip(inp: CommandInput, reason: str) -> StepResult:
        not_evaluated[key] = reason
        return StepResult(inp.projection.state)

    def base(status: str) -> dict[str, Any]:
        return {
            "status": status, "session_date": session.day, "data_as_of": run.data_as_of,
            "evaluation_run_id": run.run_id, "source_run_id": item.source_run_id, "not_evaluated_reason": item.reason,
        }

    def record(inp: CommandInput, payload: dict[str, Any]) -> None:
        inp.conn.execute(data_quality_rechecks.insert().values(
            order_id=inp.order.id, session_date=session.day, recheck_key=identity, run_id=run.run_id,
            source_run_id=item.source_run_id, data_as_of=run.data_as_of, payload=to_document(payload),
        ))
        rechecked[key] = identity

    def final(inp: CommandInput, status: str, reason: str) -> StepResult:
        record(inp, {**base(status), "terminal_reason": reason})  # consumes the pending session explicitly
        terminal[key] = reason
        return StepResult(inp.projection.state)

    def command(inp: CommandInput) -> StepResult:
        state = inp.projection.state
        if _already_rechecked(inp.conn, inp.order.id, session.day):
            return skip(inp, "ALREADY_RECHECKED")  # one row per (order, session), even for overlapping runs
        if f"DATA_QUALITY:{day}" in known_hashes(inp.conn, inp.order.id):
            return skip(inp, "ALREADY_EVALUATED")  # D4: the original snapshot exists; nothing to recheck
        window_start, window_end = quality_window(state, inp.ctx, session.close_utc)
        start, end = max(window_start, session.open_utc), min(window_end, session.close_utc)
        if end <= start:
            return final(inp, RECHECK_NOT_MEASURABLE, "OUTSIDE_WINDOW")
        if feed_failed:
            if past_final_lookback:  # D52: never let a failing feed drop out of /health without a terminal row
                final(inp, RECHECK_PROVIDER_FAILURE_FINAL, "PROVIDER_FAILURE")
                # Spec 6: the session's minutes can no longer be verified. Same locked path as flag_order_review
                # (D34); idempotent by event_key NEEDS_REVIEW:DATA_QUALITY_UNVERIFIED:<session>.
                flagged: StepResult = inp.model.flag_review(state, RECHECK_UNVERIFIED_REVIEW, day)
                return flagged
            return skip(inp, "PROVIDER_FAILURE")  # D12: retried by a later recheck
        ticker = inp.signal.spec.ticker
        minutes = inp.ctx.calendar.expected_minutes(start, end)
        bars = bars_in_minutes(
            read_bars_as_of(inp.conn, ticker, inp.order.price_source, start, end, run.data_as_of), minutes
        )
        if not bars:
            if past_final_lookback:
                return final(inp, RECHECK_NO_OBSERVATIONS_FINAL, "NO_OBSERVATIONS")
            return skip(inp, "NO_OBSERVATIONS")  # D12
        quality = next((q for q in session_quality(inp.ctx.calendar, start, end, [b.ts for b in bars])
                        if q.day == session.day), None)
        if quality is None:
            return final(inp, RECHECK_NOT_MEASURABLE, "OUTSIDE_WINDOW")

        gaps = find_gaps(inp.ctx.calendar, quality.missing, inp.order.config.data_gap_minutes)
        flags: list[ReviewFlag] = []
        level_touch_check: str | None = None
        unchecked: list[datetime] = []
        if quality.missing:
            reference = minute_reference or {}
            levels = levels_state_at_close(
                state, inp.signal.spec.stop, [stored.prepared for stored in stored_events(inp.conn, inp.order.id)],
                inp.order.fill_model_version, session.close_utc,
            )
            if levels is not None:
                level_touch_check = LEVELS_FROM_LEDGER
                flags.extend(missing_bar_reviews(quality.missing, reference, inp.signal.spec, levels))
            else:
                level_touch_check = LEVELS_NOT_DERIVABLE
                missing = sorted(quality.missing)
                flags.extend(ReviewFlag("MISSING_BAR_UNVERIFIABLE", m.isoformat()) for m in missing if m not in reference)
                unchecked = [m for m in missing if m in reference]
        if daily_reference is not None and start == session.open_utc and end == session.close_utc:
            high, low = daily_reference
            if daily_range_mismatch(bars, high, low, inp.order.config.crosscheck_tolerance_pct):
                flags.append(ReviewFlag("DAILY_RANGE_MISMATCH", day))

        record(inp, {
            **quality.event().payload,
            **base(RECHECK_EVALUATED),
            "gaps": [{"gap_start_ts": gap_start, "minutes": length} for gap_start, length in gaps],
            "review_flags": [{"reason": flag.reason, "ref": flag.ref} for flag in flags],
            "level_touch_check": level_touch_check,
            "level_touch_unchecked_minutes": unchecked,
        })
        result = StepResult(state)
        for flag in flags:
            flagged = inp.model.flag_review(result.state, flag.reason, flag.ref)
            result = StepResult(flagged.state, result.events + flagged.events)
        return result

    return command


def run_quality_recheck(
    engine: Engine,
    *,
    gateway: MarketDataGateway,
    reference: ReferenceSource,
    code_version: str,
    market_now: datetime,
) -> RecheckReport:
    now = require_aware(market_now, "market_now")
    last_closed = last_closed_session_day(now)
    with engine.connect() as conn:
        # D12(b): the just-closed session still belongs to END_OF_DAY; only older sessions are rechecked.
        pending = [item for item in pending_quality_sessions(conn)
                   if last_closed is not None and item.session_day < last_closed]
    if not pending:
        return RecheckReport(None, {}, {})

    sessions: dict[date, Session] = {item.session_day: session_for_day(item.session_day) for item in pending}
    failed_feeds: dict[str, str] = {}
    for price_source, ticker, day in sorted({(i.price_source, i.ticker, i.session_day) for i in pending}):
        feed = f"{price_source}:{ticker}:{day.isoformat()}"
        try:
            ingest_bars(engine, gateway.bar_source(price_source), ticker, sessions[day].open_utc,
                        sessions[day].close_utc)
        except UnknownDataSource:
            failed_feeds[feed] = "UNKNOWN_DATA_SOURCE"
        except (SourceUnavailable, SourceDataError) as exc:
            failed_feeds[feed] = type(exc).__name__

    unavailable: dict[str, str] = {}
    minute_refs: dict[tuple[str, date], MinuteReference] = {}
    daily_refs: dict[tuple[str, date], DailyReference] = {}
    for ticker, day in sorted({(i.ticker, i.session_day) for i in pending}):
        try:
            minute_refs[(ticker, day)] = reference.fetch_minute_bars(ticker, day)
        except (SourceUnavailable, SourceDataError) as exc:
            minute_refs[(ticker, day)] = None
            unavailable[f"{ticker}:{day.isoformat()}:1m"] = type(exc).__name__
        try:
            daily_refs[(ticker, day)] = reference.fetch_daily_range(ticker, day)
        except (SourceUnavailable, SourceDataError) as exc:
            daily_refs[(ticker, day)] = None
            unavailable[f"{ticker}:{day.isoformat()}:1d"] = type(exc).__name__

    session_days = sorted(day.isoformat() for day in sessions)
    data_as_of = acquire_data_as_of(engine)
    run: RunInfo | None = None
    try:
        with engine.begin() as conn:
            run = start_run(conn, RunKind.QUALITY_RECHECK, data_as_of, code_version,
                            detail={"sessions": session_days, "market_now": now})
        rechecked: dict[str, str] = {}
        not_evaluated: dict[str, str] = {}
        terminal: dict[str, str] = {}
        outcomes: list[OrderOutcome] = []
        final_days = {day for day in sessions if sessions_closed_after(day, now) >= RECHECK_FINAL_AFTER_SESSIONS}
        for item in pending:
            command = _recheck_command(
                run, sessions[item.session_day], item, minute_refs[(item.ticker, item.session_day)],
                daily_refs[(item.ticker, item.session_day)],
                f"{item.price_source}:{item.ticker}:{item.session_day.isoformat()}" in failed_feeds,
                item.session_day in final_days, rechecked, not_evaluated, terminal,
            )
            outcome = isolated(item.order_id, partial(apply_command, engine, item.order_id, command))
            if outcome.error is not None:
                rechecked.pop(_item_key(item), None)  # the transaction rolled back together with the row
                terminal.pop(_item_key(item), None)
            outcomes.append(outcome)
        integrity_errors, order_errors = split_errors(outcomes)
        with engine.begin() as conn:
            finish_run(conn, run.run_id, RunStatus.COMPLETED, {
                "sessions": session_days, "market_now": now, "orders": len(outcomes), "rechecked": rechecked,
                "terminal": terminal, "not_evaluated": not_evaluated, "ingest_failures": failed_feeds,
                "unavailable": unavailable, "integrity_errors": integrity_errors, "order_errors": order_errors,
            })
        return RecheckReport(run.run_id, rechecked, not_evaluated, tuple(outcomes), terminal)
    except Exception as exc:
        if run is not None:
            with engine.begin() as conn:
                finish_run(conn, run.run_id, RunStatus.FAILED, {"error": repr(exc), "market_now": now})
        raise
