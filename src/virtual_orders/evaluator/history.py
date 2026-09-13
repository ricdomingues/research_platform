"""Regenerate an order's authoritative history (spec 10, D2; Plan 1 close-out entry 1).

Domain facts (order_events) + evaluation facts (runs, segments) + market facts (bars as-of) must
reproduce every stored (event_key, payload_hash) in seq order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from types import ModuleType
from typing import Any

from sqlalchemy import Connection

from core.domain.calendar import ONE_MINUTE
from core.domain.models import Event, GatingState, OrderContext, OrderState, OrderStatus, StepResult
from core.fills import get_fill_model
from virtual_orders.evaluator.context import load_order_context
from virtual_orders.ledger.errors import HistoryDivergence, LedgerIntegrityError
from virtual_orders.ledger.events import StoredEvent, dedupe_events, stored_events
from virtual_orders.ledger.orders import OrderRow, SignalRow
from virtual_orders.ledger.runs import SegmentRow, get_run, list_segments
from virtual_orders.marketdata.asof import bars_in_minutes, read_bar_version, read_bars_as_of, selected_data_hash
from virtual_orders.storage.codec import PreparedEvent, parse_ts

CREATED_BASE_KEYS = frozenset({
    "fill_model_version", "signal", "config", "evaluation_start_ts", "valid_until_ts",
    "calendar_sessions_hash", "inherited_signal_state", "inherited_signal_state_hash",
})
RECORD_TYPES = frozenset({"DATA_QUALITY", "DATA_GAP"})


class ItemKind(StrEnum):
    CREATED = "CREATED"
    SEGMENT = "SEGMENT"
    COMMAND = "COMMAND"
    RECORD = "RECORD"


@dataclass(frozen=True)
class HistoryItem:
    kind: ItemKind
    events: tuple[PreparedEvent, ...]
    segment: SegmentRow | None = None


@dataclass(frozen=True)
class RegeneratedHistory:
    order: OrderRow
    signal: SignalRow
    ctx: OrderContext
    state: OrderState
    items: tuple[HistoryItem, ...]
    next_seq: int


def inherited_from_payload(payload: Mapping[str, Any]) -> GatingState | None:
    raw = payload.get("inherited_signal_state")
    if raw is None:
        return None
    return GatingState(
        status=OrderStatus(raw["status"]),
        zone_lost=bool(raw["zone_lost"]),
        entry_eligible_from=parse_ts(raw["entry_eligible_from"]),
        trigger_hit_at=parse_ts(raw["trigger_hit_at"]),
    )


def regenerate_created(model: ModuleType, ctx: OrderContext, payload: Mapping[str, Any]) -> StepResult:
    extra = {key: value for key, value in payload.items() if key not in CREATED_BASE_KEYS}
    result: StepResult = model.new_order_state(
        ctx, inherited=inherited_from_payload(payload), extra_payload=extra or None
    )
    return result


def _compare(stored: list[StoredEvent], seq: int, expected_count: int,
             regenerated: list[PreparedEvent], reason: str) -> None:
    window = stored[seq - 1 : seq - 1 + expected_count]
    if [s.prepared.identity() for s in window] == [p.identity() for p in regenerated]:
        return
    diff: list[dict[str, Any]] = []
    for offset in range(max(len(window), len(regenerated))):
        old = window[offset].prepared.identity() if offset < len(window) else None
        new = regenerated[offset].identity() if offset < len(regenerated) else None
        if old != new:
            diff.append({"seq": seq + offset, "stored": old and list(old), "regenerated": new and list(new)})
    raise HistoryDivergence(reason, diff)


def _command(
    conn: Connection,
    model: ModuleType,
    ctx: OrderContext,
    signal: SignalRow,
    price_source: str,
    state: OrderState,
    head: PreparedEvent,
) -> StepResult:
    payload = head.payload
    result: StepResult
    if head.type == "CANCELED":
        result = model.cancel(state, parse_ts(payload["at"]), payload["requested_by"])
    elif head.type == "FROZEN":
        result = model.freeze(state, payload["reason"], payload["ref"])
    elif head.type == "NEEDS_REVIEW":
        result = model.flag_review(state, payload["reason"], payload["ref"])
    elif head.type == "DIVIDEND":
        result = model.apply_dividend(
            state, ctx, date.fromisoformat(payload["ex_date"]), Decimal(payload["amount_per_share"]), True
        )
    elif head.type == "EXPIRED":
        result = model.apply_validity_end(state, ctx, None, ctx.valid_until_ts)
    elif head.type == "TIME_EXIT":
        if head.bar_ts is None or head.bar_batch_id is None:
            raise HistoryDivergence(f"TIME_EXIT_WITHOUT_BAR:{head.event_key}")
        try:
            bar = read_bar_version(conn, signal.spec.ticker, price_source, head.bar_ts, head.bar_batch_id)
        except LookupError as exc:
            raise HistoryDivergence(f"TIME_EXIT_BAR_MISSING:{head.event_key}") from exc
        result = model.apply_validity_end(state, ctx, bar, ctx.valid_until_ts)
    else:
        raise HistoryDivergence(f"UNEXPECTED_EVENT_OUTSIDE_SEGMENT:{head.event_key}")
    return result


def regenerate_history(conn: Connection, order: OrderRow) -> RegeneratedHistory:
    signal, ctx = load_order_context(conn, order)
    model = get_fill_model(order.fill_model_version)
    ticker = signal.spec.ticker
    stored = stored_events(conn, order.id)
    if [s.seq for s in stored] != list(range(1, len(stored) + 1)):
        raise HistoryDivergence("SEQ_NOT_CONTIGUOUS")
    if not stored or stored[0].prepared.type != "ORDER_CREATED":
        raise HistoryDivergence("ORDER_CREATED_MISSING")

    known: dict[str, str] = {}

    def prepare(events: Iterable[Event | PreparedEvent]) -> list[PreparedEvent]:
        try:
            return dedupe_events(known, events, order_id=order.id, evaluation_start_ts=order.evaluation_start_ts)
        except LedgerIntegrityError as exc:
            raise HistoryDivergence(f"REGENERATED_{exc.kind}") from exc

    created = regenerate_created(model, ctx, stored[0].prepared.payload)
    created_events = prepare(created.events)
    _compare(stored, 1, 1, created_events, "ORDER_CREATED")
    state = created.state
    items = [HistoryItem(ItemKind.CREATED, tuple(created_events))]
    seq = 2
    segments = list_segments(conn, order.id)
    index = 0

    while True:
        if index < len(segments) and segments[index].first_seq == seq:
            segment = segments[index]
            index += 1
            run = get_run(conn, segment.run_id)
            end = segment.bar_to + ONE_MINUTE
            minutes = ctx.calendar.expected_minutes(segment.bar_from, end)
            bars = bars_in_minutes(
                read_bars_as_of(conn, ticker, order.price_source, segment.bar_from, end, run.data_as_of), minutes
            )
            data_hash = selected_data_hash(order.price_source, ticker, minutes, bars)
            if data_hash != segment.selected_data_hash:
                raise HistoryDivergence("SELECTED_DATA_HASH_MISMATCH", [{
                    "run_id": str(segment.run_id), "bar_from": segment.bar_from.isoformat(),
                    "stored": segment.selected_data_hash, "regenerated": data_hash,
                }])
            result = model.run_bars(state, bars, ctx)
            events = prepare(result.events)
            _compare(stored, seq, segment.event_count, events, "SEGMENT")
            state = result.state
            seq += len(events)
            items.append(HistoryItem(ItemKind.SEGMENT, tuple(events), segment))
            continue
        if seq > len(stored):
            break
        head = stored[seq - 1].prepared
        if head.type in RECORD_TYPES:
            items.append(HistoryItem(ItemKind.RECORD, tuple(prepare([head]))))
            seq += 1
            continue
        result = _command(conn, model, ctx, signal, order.price_source, state, head)
        events = prepare(result.events)
        if not events:
            raise HistoryDivergence(f"COMMAND_PRODUCED_NO_EVENTS:{head.event_key}")
        _compare(stored, seq, len(events), events, "COMMAND")
        state = result.state
        seq += len(events)
        items.append(HistoryItem(ItemKind.COMMAND, tuple(events)))

    if index < len(segments):
        raise HistoryDivergence("ORPHAN_SEGMENT", [
            {"run_id": str(s.run_id), "first_seq": s.first_seq} for s in segments[index:]
        ])
    return RegeneratedHistory(order, signal, ctx, state, tuple(items), seq)
