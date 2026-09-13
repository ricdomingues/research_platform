"""Replay (spec 3.6): REPRODUCE regenerates recorded history into a new order; nothing existing changes."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, select

from core.domain.models import FillConfig
from core.fills import get_fill_model
from virtual_orders.evaluator.context import build_order_context
from virtual_orders.evaluator.history import regenerate_created, regenerate_history
from virtual_orders.ledger.errors import REPRODUCE_DIVERGENCE, HistoryDivergence, LedgerIntegrityError, OrderNotFound
from virtual_orders.ledger.events import insert_event, stored_events
from virtual_orders.ledger.orders import (
    Projection,
    get_order,
    get_signal,
    insert_order,
    lock_order,
    save_projection,
)
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import RunKind, RunStatus, SegmentRow, finish_run, record_segment, start_run
from virtual_orders.ledger.writes import apply_result, persist_new_order
from virtual_orders.marketdata.asof import (
    acquire_data_as_of,
    bars_in_minutes,
    floor_minute,
    read_bars_as_of,
    selected_data_hash,
)
from virtual_orders.marketdata.snapshots import create_or_reuse_snapshot
from virtual_orders.storage.codec import config_from_snapshot, config_to_snapshot, to_document
from virtual_orders.storage.tables import dividends, orders

ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
REJECTED_OVERRIDES = frozenset({"dividend_tolerance"})  # RECALCULATE credits the stored validated dividend row


class ReplayMode(StrEnum):
    REPRODUCE = "REPRODUCE"
    RECALCULATE = "RECALCULATE"


class ReplaySelectionError(ValueError):
    pass


@dataclass(frozen=True)
class ReplayReport:
    run_id: UUID
    mode: ReplayMode
    created: dict[UUID, UUID]
    failures: dict[UUID, str]


def select_source_orders(
    conn: Connection,
    *,
    order_ids: Sequence[UUID] | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> list[UUID]:
    has_interval = created_from is not None or created_to is not None
    if order_ids is not None and has_interval:
        raise ReplaySelectionError("use order_ids or an interval, not both")
    if order_ids is not None:
        unique = list(dict.fromkeys(order_ids))
        replays = sorted(
            str(order_id)
            for order_id in conn.execute(
                select(orders.c.id).where(orders.c.id.in_(unique), orders.c.replay.is_(True))
            ).scalars()
        )
        if replays:
            raise ReplaySelectionError(f"replay orders cannot be replayed: {', '.join(replays)}")
        return unique
    if created_from is None or created_to is None:
        raise ReplaySelectionError("an interval needs both created_from and created_to")
    return list(conn.execute(
        select(orders.c.id)
        .where(orders.c.replay.is_(False), orders.c.created_at >= created_from, orders.c.created_at < created_to)
        .order_by(orders.c.created_at, orders.c.id)
    ).scalars())


def reproduce_order(engine: Engine, source_id: UUID, *, code_version: str) -> UUID:
    def operation() -> UUID:
        with engine.begin() as conn:
            source = lock_order(conn, source_id)
            try:
                history = regenerate_history(conn, source)
            except HistoryDivergence as divergence:
                raise LedgerIntegrityError(
                    REPRODUCE_DIVERGENCE, f"history cannot be reproduced: {divergence.reason}",
                    order_id=source_id, detail={"reason": divergence.reason, "diff": divergence.diff},
                ) from divergence
            replay_order = replace(
                source, id=uuid4(), code_version=code_version, replay=True,
                replay_mode=ReplayMode.REPRODUCE.value, replay_of_order_id=source.id,
            )
            insert_order(conn, replay_order)
            seq = 1
            for item in history.items:
                if item.segment is not None:
                    record_segment(conn, replace(item.segment, order_id=replay_order.id, first_seq=seq))
                for prepared in item.events:
                    insert_event(conn, replay_order.id, seq, prepared)
                    seq += 1
            save_projection(conn, replay_order, history.signal.spec.direction, Projection(history.state, seq))
            return replay_order.id

    return run_guarded(engine, operation)


def _run_replay_batch(
    engine: Engine,
    run_id: UUID,
    mode: ReplayMode,
    source_ids: Sequence[UUID],
    replay_one: Callable[[UUID], UUID],
) -> ReplayReport:
    """Per-source isolation and run finalization shared by REPRODUCE and RECALCULATE.

    A known failure (`OrderNotFound`, `LedgerIntegrityError`) or any other `Exception` raised
    while replaying one source is recorded against that source and never aborts the batch; the
    run always reaches a final status (COMPLETED normally, FAILED + re-raise only if something
    outside this per-source handling breaks).
    """
    created: dict[UUID, UUID] = {}
    failures: dict[UUID, str] = {}
    try:
        for source_id in source_ids:
            try:
                created[source_id] = replay_one(source_id)
            except OrderNotFound:
                failures[source_id] = ORDER_NOT_FOUND
            except LedgerIntegrityError as error:
                failures[source_id] = error.kind
            except Exception as exc:  # noqa: BLE001 - one bad source must not abort the whole batch
                failures[source_id] = f"ERROR:{type(exc).__name__}"
        with engine.begin() as conn:
            finish_run(conn, run_id, RunStatus.COMPLETED, {
                "created": {str(k): v for k, v in created.items()},
                "failures": {str(k): v for k, v in failures.items()},
            })
    except Exception as exc:  # noqa: BLE001 - the run must always reach a final status
        with engine.begin() as conn:
            finish_run(conn, run_id, RunStatus.FAILED, {
                "error": repr(exc),
                "created": {str(k): v for k, v in created.items()},
                "failures": {str(k): v for k, v in failures.items()},
            })
        raise
    return ReplayReport(run_id, mode, created, failures)


def reproduce_orders(
    engine: Engine,
    *,
    code_version: str,
    order_ids: Sequence[UUID] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> ReplayReport:
    with engine.connect() as conn:
        source_ids = select_source_orders(conn, order_ids=order_ids, created_from=created_from, created_to=created_to)
    data_as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.REPLAY, data_as_of, code_version,
                        detail={"mode": ReplayMode.REPRODUCE.value, "sources": source_ids})
    return _run_replay_batch(
        engine, run.run_id, ReplayMode.REPRODUCE, source_ids,
        lambda source_id: reproduce_order(engine, source_id, code_version=code_version),
    )


def validate_recalculation_request(
    fill_model_version: str | None, config_overrides: Mapping[str, Any] | None
) -> None:
    """Rejects a RECALCULATE request up front (D15) instead of failing per source inside the batch."""
    if fill_model_version is not None:
        try:
            get_fill_model(fill_model_version)
        except KeyError as exc:
            raise ReplaySelectionError(f"unknown fill_model_version: {fill_model_version}") from exc
    overrides = dict(config_overrides or {})
    unknown = sorted(set(overrides) - {f.name for f in fields(FillConfig)})
    if unknown:
        raise ReplaySelectionError(f"unknown config_overrides: {', '.join(unknown)}")
    rejected = sorted(set(overrides) & REJECTED_OVERRIDES)
    if rejected:
        raise ReplaySelectionError(f"config_overrides not applied by RECALCULATE: {', '.join(rejected)}")
    try:
        config_from_snapshot({**config_to_snapshot(FillConfig()), **to_document(overrides)})
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ReplaySelectionError(f"invalid config_overrides: {exc}") from exc


def _chunks_by_dividend(
    minutes: Sequence[datetime], session_day_of: Mapping[datetime, date], dividend_days: set[date]
) -> list[tuple[date | None, list[datetime]]]:
    """Split expected minutes so each validated/unvalidated dividend is applied at its ex-date open."""
    chunks: list[tuple[date | None, list[datetime]]] = []
    pending: date | None = None
    current: list[datetime] = []
    applied: set[date] = set()
    for minute in minutes:
        day = session_day_of[minute]
        if day in dividend_days and day not in applied:
            chunks.append((pending, current))
            pending, current = day, []
            applied.add(day)
        current.append(minute)
    chunks.append((pending, current))
    return chunks


def _recalculate_order(
    engine: Engine,
    source_id: UUID,
    *,
    code_version: str,
    data_as_of: datetime,
    fill_model_version: str | None = None,
    config_overrides: Mapping[str, Any] | None = None,
) -> UUID:
    def operation() -> UUID:
        with engine.begin() as conn:
            source = get_order(conn, source_id)
            signal = get_signal(conn, source.signal_id)
            ticker, direction = signal.spec.ticker, signal.spec.direction
            version = fill_model_version or source.fill_model_version
            model = get_fill_model(version)
            config = config_from_snapshot({**config_to_snapshot(source.config),
                                           **to_document(dict(config_overrides or {}))})
            snapshot_id = create_or_reuse_snapshot(
                conn, source=source.price_source, data_as_of=data_as_of, tickers=[ticker],
                range_from=source.evaluation_start_ts, range_to=source.valid_until_ts,
            )
            order = replace(
                source, id=uuid4(), fill_model_version=version, config=config, code_version=code_version,
                risk_amount=config.risk_amount, replay=True, replay_mode=ReplayMode.RECALCULATE.value,
                replay_of_order_id=source.id, market_data_snapshot_id=snapshot_id,
            )
            ctx = build_order_context(signal, order)
            source_created = stored_events(conn, source.id)[0].prepared.payload
            created = regenerate_created(model, ctx, source_created)
            next_seq = persist_new_order(conn, order, direction, created).next_seq
            state = created.state

            start = order.evaluation_start_ts
            horizon = min(order.valid_until_ts, floor_minute(data_as_of))
            minutes = ctx.calendar.expected_minutes(start, horizon) if horizon > start else []
            bars = bars_in_minutes(
                read_bars_as_of(conn, ticker, order.price_source, start, horizon, data_as_of), minutes
            )
            dividend_rows = {
                row.ex_date: row
                for row in conn.execute(
                    select(dividends).where(dividends.c.ticker == ticker, dividends.c.checked_at <= data_as_of)
                )
            }
            session_day_of = {}
            for minute in minutes:
                session = ctx.calendar.session_containing(minute)
                if session is None:
                    raise RuntimeError(f"calendar inconsistency at {minute.isoformat()}")
                session_day_of[minute] = session.day

            for dividend_day, chunk in _chunks_by_dividend(minutes, session_day_of, set(dividend_rows)):
                if dividend_day is not None:
                    row = dividend_rows[dividend_day]
                    paid = model.apply_dividend(state, ctx, row.ex_date, row.amount, row.validated)
                    next_seq = apply_result(conn, order, direction, next_seq, paid).next_seq
                    state = paid.state
                if not chunk:
                    continue
                run = start_run(conn, RunKind.REPLAY, data_as_of, code_version,
                                detail={"mode": ReplayMode.RECALCULATE.value, "source_order_id": source.id})
                finish_run(conn, run.run_id, RunStatus.COMPLETED, {"order_id": order.id})
                used = [item for item in bars if chunk[0] <= item.ts <= chunk[-1]]
                result = model.run_bars(state, used, ctx)
                appended = apply_result(conn, order, direction, next_seq, result)
                record_segment(conn, SegmentRow(
                    order.id, run.run_id, chunk[0], chunk[-1],
                    selected_data_hash(order.price_source, ticker, chunk, used),
                    appended.first_seq, len(appended.inserted),
                ))
                next_seq, state = appended.next_seq, result.state

            if data_as_of >= order.valid_until_ts and not state.is_final and not state.frozen:
                last_bar = next((item for item in reversed(bars) if item.ts == state.last_bar_ts), None)
                ended = model.apply_validity_end(state, ctx, last_bar, data_as_of)
                apply_result(conn, order, direction, next_seq, ended)
            return order.id

    return run_guarded(engine, operation)


def recalculate_orders(
    engine: Engine,
    *,
    code_version: str,
    order_ids: Sequence[UUID] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    fill_model_version: str | None = None,
    config_overrides: Mapping[str, Any] | None = None,
    data_as_of: datetime | None = None,
) -> ReplayReport:
    if data_as_of is not None and data_as_of.tzinfo is None:
        raise ReplaySelectionError("data_as_of must be timezone-aware")
    validate_recalculation_request(fill_model_version, config_overrides)
    with engine.connect() as conn:
        source_ids = select_source_orders(conn, order_ids=order_ids, created_from=created_from, created_to=created_to)
    watermark = acquire_data_as_of(engine)
    if data_as_of is not None and data_as_of > watermark:
        raise ReplaySelectionError("data_as_of cannot be later than the ingestion watermark")
    as_of = (data_as_of or watermark).astimezone(UTC)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.REPLAY, as_of, code_version, detail={
            "mode": ReplayMode.RECALCULATE.value, "sources": source_ids,
            "fill_model_version": fill_model_version, "config_overrides": dict(config_overrides or {}),
        })
    return _run_replay_batch(
        engine, run.run_id, ReplayMode.RECALCULATE, source_ids,
        lambda source_id: _recalculate_order(
            engine, source_id, code_version=code_version, data_as_of=as_of,
            fill_model_version=fill_model_version, config_overrides=config_overrides,
        ),
    )
