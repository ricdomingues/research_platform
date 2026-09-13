"""End-of-day job pieces (spec 4.6, 5.3) with DATA_QUALITY immutability (spec 10, D4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, Engine, select, text

from core.dataquality import (
    ReviewFlag,
    daily_range_mismatch,
    find_gaps,
    gap_events,
    missing_bar_reviews,
    quality_window,
    session_quality,
)
from core.domain.calendar import Session
from core.domain.models import Bar, Event, EventType, StepResult
from virtual_orders.evaluator.commands import CommandInput, apply_command, expire_due_orders
from virtual_orders.evaluator.cycle import CycleReport, run_live_cycle
from virtual_orders.evaluator.outcomes import OrderOutcome
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of, bars_in_minutes, read_bars_as_of
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.sources import ReferenceSource, SourceDataError, SourceUnavailable
from virtual_orders.storage.tables import order_events

_CANDIDATES = text(
    """
    SELECT o.id AS order_id, g.ticker AS ticker
    FROM orders o JOIN order_state st ON st.order_id = o.id JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND o.evaluation_start_ts < :session_close
      AND (st.final_event_ts IS NULL OR st.final_event_ts >= :session_open)
    ORDER BY g.ticker, o.id
    """
)


@dataclass(frozen=True)
class QualityReport:
    run_id: UUID
    session_day: date
    outcomes: tuple[OrderOutcome, ...]
    unavailable: dict[str, str]


@dataclass(frozen=True)
class EndOfDayReport:
    cycle: CycleReport
    expired: tuple[OrderOutcome, ...]
    quality: QualityReport


def session_for_day(day: date) -> Session:
    probe = datetime.combine(day, time(12), tzinfo=UTC)
    session = next((s for s in calendar_for_window(probe, probe).sessions if s.day == day), None)
    if session is None:
        raise ValueError(f"{day} is not a trading session")
    return session


def _already_emitted(conn: Connection, order_id: UUID, event_key: str) -> bool:
    return conn.execute(
        select(order_events.c.id).where(order_events.c.order_id == order_id, order_events.c.event_key == event_key)
    ).first() is not None


def _quality_command(
    run: RunInfo,
    session: Session,
    minute_reference: dict[datetime, Bar] | None,
    daily_reference: tuple[Decimal, Decimal] | None,
) -> Callable[[CommandInput], StepResult]:
    def command(inp: CommandInput) -> StepResult:
        state = inp.projection.state
        key = f"DATA_QUALITY:{session.day.isoformat()}"
        if _already_emitted(inp.conn, inp.order.id, key):
            return StepResult(state)  # D4: never re-emitted
        window_start, window_end = quality_window(state, inp.ctx, session.close_utc)
        start, end = max(window_start, session.open_utc), min(window_end, session.close_utc)
        if end <= start:
            return StepResult(state)
        ticker = inp.signal.spec.ticker
        minutes = inp.ctx.calendar.expected_minutes(start, end)
        source_id = inp.order.price_source
        bars = bars_in_minutes(read_bars_as_of(inp.conn, ticker, source_id, start, end, run.data_as_of), minutes)
        quality = next((q for q in session_quality(inp.ctx.calendar, start, end, [b.ts for b in bars])
                        if q.day == session.day), None)
        if quality is None:
            return StepResult(state)

        base = quality.event()
        emitted: list[Event] = [Event(
            EventType.DATA_QUALITY, base.event_key,
            payload={**base.payload, "data_as_of": run.data_as_of, "evaluation_run_id": run.run_id},
        )]
        emitted.extend(gap_events(find_gaps(inp.ctx.calendar, quality.missing, inp.order.config.data_gap_minutes)))

        flags: list[ReviewFlag] = []
        if quality.missing:
            flags.extend(missing_bar_reviews(quality.missing, minute_reference or {}, inp.signal.spec, state))
        covers_session = start == session.open_utc and end == session.close_utc
        if daily_reference is not None and bars and covers_session:
            high, low = daily_reference
            if daily_range_mismatch(bars, high, low, inp.order.config.crosscheck_tolerance_pct):
                flags.append(ReviewFlag("DAILY_RANGE_MISMATCH", session.day.isoformat()))

        result = StepResult(state, tuple(emitted))
        for flag in flags:
            flagged = inp.model.flag_review(result.state, flag.reason, flag.ref)
            result = StepResult(flagged.state, result.events + flagged.events)
        return result

    return command


def run_session_quality(
    engine: Engine, *, session_day: date, reference: ReferenceSource, code_version: str
) -> QualityReport:
    session = session_for_day(session_day)
    data_as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.END_OF_DAY, data_as_of, code_version, detail={"session_day": session_day})
    with engine.connect() as conn:
        candidates = conn.execute(
            _CANDIDATES, {"session_open": session.open_utc, "session_close": session.close_utc}
        ).all()

    unavailable: dict[str, str] = {}
    minute_refs: dict[str, dict[datetime, Bar] | None] = {}
    daily_refs: dict[str, tuple[Decimal, Decimal] | None] = {}
    for ticker in sorted({row.ticker for row in candidates}):
        try:
            minute_refs[ticker] = reference.fetch_minute_bars(ticker, session_day)
        except (SourceUnavailable, SourceDataError) as exc:
            minute_refs[ticker] = None
            unavailable[f"{ticker}:1m"] = str(exc)
        try:
            daily_refs[ticker] = reference.fetch_daily_range(ticker, session_day)
        except (SourceUnavailable, SourceDataError) as exc:
            daily_refs[ticker] = None
            unavailable[f"{ticker}:1d"] = str(exc)

    outcomes = tuple(
        apply_command(engine, row.order_id,
                      _quality_command(run, session, minute_refs[row.ticker], daily_refs[row.ticker]))
        for row in candidates
    )
    with engine.begin() as conn:
        finish_run(conn, run.run_id, RunStatus.COMPLETED,
                   {"session_day": session_day, "orders": len(outcomes), "unavailable": unavailable})
    return QualityReport(run.run_id, session_day, outcomes, unavailable)


def run_end_of_day(
    engine: Engine,
    *,
    gateway: MarketDataGateway,
    reference: ReferenceSource,
    session_day: date,
    code_version: str,
    market_now: datetime,
) -> EndOfDayReport:
    cycle = run_live_cycle(engine, gateway, code_version=code_version, market_now=market_now,
                           close_trailing_gap=True)
    expired = tuple(expire_due_orders(engine, now=market_now))
    quality = run_session_quality(engine, session_day=session_day, reference=reference, code_version=code_version)
    return EndOfDayReport(cycle, expired, quality)
