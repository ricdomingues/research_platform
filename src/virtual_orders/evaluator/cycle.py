"""Live evaluator cycle (spec 5.3): ingest -> watermark -> per-order locked evaluation -> segment."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from core.fills import get_fill_model
from virtual_orders.evaluator.context import load_order_context
from virtual_orders.evaluator.outcomes import OrderOutcome
from virtual_orders.ledger.errors import PROJECTION_MISSING, LedgerIntegrityError
from virtual_orders.ledger.orders import load_projection, lock_order
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import (
    RunInfo,
    RunKind,
    RunStatus,
    SegmentRow,
    finish_run,
    last_segment_end,
    record_segment,
    start_run,
)
from virtual_orders.ledger.writes import apply_result
from virtual_orders.marketdata.asof import (
    acquire_data_as_of,
    bars_in_minutes,
    floor_minute,
    read_bars_as_of,
    selected_data_hash,
)
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable
from virtual_orders.storage.database import CYCLE_LOCK_KEY

_OPEN_ORDERS = text(
    """
    SELECT o.id AS order_id, o.price_source AS price_source, g.ticker AS ticker,
           COALESCE(MAX(s.bar_to) + interval '1 minute', o.evaluation_start_ts) AS resume_from
    FROM orders o
    JOIN order_state st ON st.order_id = o.id
    JOIN signals g ON g.id = o.signal_id
    LEFT JOIN order_eval_segments s ON s.order_id = o.id
    WHERE NOT o.replay AND NOT st.frozen AND st.status IN ('PENDING', 'OPEN', 'PARTIAL')
    GROUP BY o.id, o.price_source, g.ticker, o.evaluation_start_ts
    ORDER BY o.price_source, g.ticker, o.id
    """
)


@dataclass(frozen=True)
class OpenOrderCursor:
    order_id: UUID
    price_source: str
    ticker: str
    resume_from: datetime


@dataclass(frozen=True)
class CycleReport:
    skipped: bool
    run_id: UUID | None = None
    data_as_of: datetime | None = None
    outcomes: tuple[OrderOutcome, ...] = ()
    ingest_failures: dict[str, str] = field(default_factory=dict)


def try_cycle_lock(conn: Connection) -> bool:
    return bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": CYCLE_LOCK_KEY}).scalar_one())


def release_cycle_lock(conn: Connection) -> None:
    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": CYCLE_LOCK_KEY})


def open_order_cursors(conn: Connection) -> list[OpenOrderCursor]:
    return [
        OpenOrderCursor(row.order_id, row.price_source, row.ticker, row.resume_from)
        for row in conn.execute(_OPEN_ORDERS)
    ]


def evaluate_order(
    engine: Engine, order_id: UUID, run: RunInfo, *, market_now: datetime, close_trailing_gap: bool = False
) -> OrderOutcome:
    def operation() -> OrderOutcome:
        with engine.begin() as conn:
            order = lock_order(conn, order_id)
            projection = load_projection(conn, order_id)
            if projection is None:
                raise LedgerIntegrityError(
                    PROJECTION_MISSING, "order_state missing; rebuild before evaluating", order_id=order_id
                )
            state = projection.state
            if order.replay or state.is_final or state.frozen:
                return OrderOutcome(order_id)
            signal, ctx = load_order_context(conn, order)
            last_to = last_segment_end(conn, order_id)
            start = ctx.evaluation_start_ts if last_to is None else ctx.calendar.next_expected_minute(last_to)
            horizon = min(ctx.valid_until_ts, floor_minute(market_now))
            if start >= horizon:
                return OrderOutcome(order_id)
            minutes = ctx.calendar.expected_minutes(start, horizon)
            ticker = signal.spec.ticker
            bars = bars_in_minutes(
                read_bars_as_of(conn, ticker, order.price_source, start, horizon, run.data_as_of), minutes
            )
            if close_trailing_gap:
                bar_to = minutes[-1] if minutes else None
            else:
                bar_to = bars[-1].ts if bars else None
            if bar_to is None:
                return OrderOutcome(order_id)
            window = [minute for minute in minutes if minute <= bar_to]
            used = [item for item in bars if item.ts <= bar_to]
            data_hash = selected_data_hash(order.price_source, ticker, window, used)
            result = get_fill_model(order.fill_model_version).run_bars(state, used, ctx)
            appended = apply_result(conn, order, signal.spec.direction, projection.next_seq, result)
            record_segment(
                conn,
                SegmentRow(
                    order_id, run.run_id, window[0], bar_to, data_hash, appended.first_seq, len(appended.inserted)
                ),
            )
            return OrderOutcome(order_id, tuple(p.event_key for p in appended.inserted), (window[0], bar_to))

    try:
        return run_guarded(engine, operation)
    except LedgerIntegrityError as error:
        return OrderOutcome(order_id, error=error.kind)


def run_live_cycle(
    engine: Engine,
    gateway: MarketDataGateway,
    *,
    code_version: str,
    market_now: datetime | None = None,
    close_trailing_gap: bool = False,
) -> CycleReport:
    started = datetime.now(UTC)
    now = (market_now or started).astimezone(UTC)
    lock_conn = engine.connect()
    try:
        if not try_cycle_lock(lock_conn):
            lock_conn.rollback()
            return CycleReport(skipped=True)
        lock_conn.commit()
        run: RunInfo | None = None
        try:
            with engine.connect() as conn:
                cursors = open_order_cursors(conn)
            resume: dict[tuple[str, str], datetime] = {}
            for cursor in cursors:
                key = (cursor.price_source, cursor.ticker)
                resume[key] = min(resume.get(key, cursor.resume_from), cursor.resume_from)
            failures: dict[str, str] = {}
            for (price_source, ticker), begin in sorted(resume.items()):
                feed_key = f"{price_source}:{ticker}"
                try:
                    ingest_bars(engine, gateway.bar_source(price_source), ticker, begin, floor_minute(now))
                except UnknownDataSource as exc:
                    failures[feed_key] = f"UNKNOWN_DATA_SOURCE: {exc}"
                except (SourceUnavailable, SourceDataError) as exc:
                    failures[feed_key] = str(exc)
            data_as_of = acquire_data_as_of(engine)
            with engine.begin() as conn:
                run = start_run(conn, RunKind.LIVE, data_as_of, code_version, started_at=started)
            outcomes = tuple(
                evaluate_order(engine, cursor.order_id, run, market_now=now, close_trailing_gap=close_trailing_gap)
                for cursor in cursors
                if f"{cursor.price_source}:{cursor.ticker}" not in failures
            )
            with engine.begin() as conn:
                finish_run(
                    conn,
                    run.run_id,
                    RunStatus.COMPLETED,
                    {
                        "orders": len(outcomes),
                        "ingest_failures": failures,
                        "integrity_errors": {str(o.order_id): o.error for o in outcomes if o.error},
                        "market_now": now,
                    },
                )
            return CycleReport(False, run.run_id, data_as_of, outcomes, failures)
        except Exception as exc:
            if run is not None:
                with engine.begin() as conn:
                    finish_run(conn, run.run_id, RunStatus.FAILED, {"error": repr(exc)})
            raise
        finally:
            release_cycle_lock(lock_conn)
            lock_conn.commit()
    finally:
        lock_conn.close()
