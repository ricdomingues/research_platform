"""fill_model v1 (spec section 4). FROZEN: behavior changes require a new version module.

Decimal arithmetic runs under V1_CONTEXT (prec=28, ROUND_HALF_EVEN), never the ambient
context; this context is part of the frozen v1 semantics.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from functools import wraps
from typing import Any, Callable, Iterable, Mapping, TypeVar

from core.domain.hashing import sha256_hex
from core.domain.models import (
    ZERO,
    Bar,
    CloseReason,
    Direction,
    EntryPath,
    Event,
    EventType,
    GatingState,
    OrderContext,
    OrderState,
    OrderStatus,
    SignalSpec,
    StepResult,
    ZoneLostPolicy,
    coerce_decimal,
)
from core.fills.v1.mirror import mirror_bar, mirror_event, mirror_signal, mirror_state

VERSION = "v1"
BPS = Decimal("10000")
V1_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)

_F = TypeVar("_F", bound=Callable[..., Any])


def _in_v1_context(func: _F) -> _F:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with localcontext(V1_CONTEXT):
            return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _adverse_buy(price: Decimal, bps: Decimal) -> Decimal:
    return price + abs(price) * bps / BPS


def _adverse_sell(price: Decimal, bps: Decimal) -> Decimal:
    return price - abs(price) * bps / BPS


def _min(current: Decimal | None, value: Decimal) -> Decimal:
    return value if current is None else min(current, value)


def _max(current: Decimal | None, value: Decimal) -> Decimal:
    return value if current is None else max(current, value)


def _execution_cost(ctx: OrderContext, price: Decimal, qty: Decimal, leg: str) -> Decimal:
    config = ctx.config
    cost = config.commission_per_execution
    if config.sec_taf_fees_enabled:
        is_long = ctx.signal.direction is Direction.LONG
        is_sell = (leg == "exit") if is_long else (leg == "entry")
        if is_sell:
            cost += abs(price) * qty * config.sec_fee_rate
            cost += min(qty * config.taf_fee_per_share, config.taf_fee_max)
    return cost


@_in_v1_context
def new_order_state(
    ctx: OrderContext,
    inherited: GatingState | None = None,
    extra_payload: Mapping[str, Any] | None = None,
) -> StepResult:
    state = OrderState(initial_stop=ctx.signal.stop, stop_current=ctx.signal.stop)
    inherited_payload = None
    inherited_hash = None
    if inherited is not None:
        if inherited.status is not OrderStatus.PENDING:
            raise ValueError("inherited signal state must be PENDING")
        state = replace(
            state,
            zone_lost=inherited.zone_lost,
            zone_ever_lost=inherited.zone_lost or inherited.entry_eligible_from is not None,  # entry_eligible_from only set by ZONE_RECLAIMED
            entry_eligible_from=inherited.entry_eligible_from,
            trigger_hit_at=inherited.trigger_hit_at,
        )
        inherited_payload = inherited.as_payload()
        inherited_hash = sha256_hex(inherited_payload)
    payload: dict[str, Any] = {
        "fill_model_version": VERSION,
        "signal": ctx.signal.as_payload(),
        "config": ctx.config.snapshot(),
        "evaluation_start_ts": ctx.evaluation_start_ts,
        "valid_until_ts": ctx.valid_until_ts,
        "inherited_signal_state": inherited_payload,
        "inherited_signal_state_hash": inherited_hash,
    }
    if extra_payload:
        overlap = set(extra_payload) & set(payload)
        if overlap:
            raise ValueError(f"extra_payload overrides reserved keys: {sorted(overlap)}")
        payload.update(extra_payload)
    return StepResult(state, (Event(EventType.ORDER_CREATED, "ORDER_CREATED", payload=payload),))


@_in_v1_context
def step(state: OrderState, bar: Bar, ctx: OrderContext) -> StepResult:
    if state.is_final or state.frozen:
        return StepResult(state)
    if bar.ts < ctx.evaluation_start_ts or bar.ts >= ctx.valid_until_ts:
        return StepResult(state)
    if state.last_bar_ts is not None and bar.ts <= state.last_bar_ts:
        return StepResult(state)
    if not ctx.calendar.is_expected_minute(bar.ts):
        return StepResult(state)

    is_long = ctx.signal.direction is Direction.LONG
    signal = ctx.signal if is_long else mirror_signal(ctx.signal)
    work_bar = bar if is_long else mirror_bar(bar)
    work_state = state if is_long else mirror_state(state)

    if work_state.status is OrderStatus.PENDING:
        work_state, events = _entry_phase(work_state, work_bar, signal, ctx)
    else:
        work_state, events = _exit_phase(work_state, work_bar, signal, ctx, entry_bar=False)
    work_state = replace(work_state, last_bar_ts=bar.ts)
    if not work_state.is_final and bar.ts == ctx.calendar.last_expected_minute_before(ctx.valid_until_ts):
        work_state, end_events = _validity_end(work_state, work_bar, ctx)
        events = events + end_events

    if not is_long:
        work_state = mirror_state(work_state)
        events = [mirror_event(event) for event in events]
    return StepResult(work_state, tuple(events))


@_in_v1_context
def run_bars(state: OrderState, bars: Iterable[Bar], ctx: OrderContext) -> StepResult:
    events: list[Event] = []
    for current in sorted(bars, key=lambda b: b.ts):
        result = step(state, current, ctx)
        state = result.state
        events.extend(result.events)
    return StepResult(state, tuple(events))


def _eligible(state: OrderState, bar: Bar, signal: SignalSpec, ctx: OrderContext) -> bool:
    if state.zone_lost:
        return False
    if state.entry_eligible_from is not None and bar.ts < state.entry_eligible_from:
        return False
    if signal.trigger_price is not None:
        if state.trigger_hit_at is None:
            return False
        if bar.ts < ctx.calendar.next_expected_minute(state.trigger_hit_at):
            return False
    return True


def _invalidate(state: OrderState, bar: Bar, reason: str) -> tuple[OrderState, list[Event]]:
    event = Event(
        EventType.INVALIDATED, "INVALIDATED", bar.ts,
        bar_batch_id=bar.batch_id, payload={"reason": reason},
    )
    return replace(state, status=OrderStatus.INVALIDATED, final_event_ts=bar.ts), [event]


def _entry_phase(
    state: OrderState, bar: Bar, signal: SignalSpec, ctx: OrderContext
) -> tuple[OrderState, list[Event]]:
    # Rule 1
    if bar.open <= signal.stop:
        return _invalidate(state, bar, "OPEN_AT_OR_THROUGH_STOP")

    # Rules 2 and 3
    if _eligible(state, bar, signal, ctx):
        raw_price: Decimal | None = None
        rule = ""
        if signal.entry_zone_low <= bar.open <= signal.entry_zone_high:
            raw_price, rule = bar.open, "ZONE_OPEN"
        elif bar.open > signal.entry_zone_high and bar.low < signal.entry_zone_high:
            raw_price, rule = signal.entry_zone_high, "ZONE_CROSS"
        if raw_price is not None:
            state, fill_events = _fill(state, bar, signal, ctx, raw_price, rule)
            state, exit_events = _exit_phase(state, bar, signal, ctx, entry_bar=True)
            return state, fill_events + exit_events

    # Rule 4
    if bar.low <= signal.stop:
        return _invalidate(state, bar, "STOP_TOUCHED_WITHOUT_POSITION")

    events: list[Event] = []
    # Rule 5: support lost only by open or close below the zone
    if not state.zone_lost and (bar.open < signal.entry_zone_low or bar.close < signal.entry_zone_low):
        events.append(
            Event(
                EventType.ZONE_LOST, f"ZONE_LOST:{bar.ts.isoformat()}", bar.ts,
                bar_batch_id=bar.batch_id,
                payload={"zone_boundary_level": signal.entry_zone_low},
            )
        )
        state = replace(state, zone_lost=True, zone_ever_lost=True)
        if ctx.config.zone_lost_policy is ZoneLostPolicy.CANCEL:
            state, invalidated = _invalidate(state, bar, "ZONE_LOST_CANCEL")
            return state, events + invalidated

    # Rule 6
    if state.zone_lost and bar.close >= signal.entry_zone_low:
        eligible_from = ctx.calendar.next_expected_minute(bar.ts)
        events.append(
            Event(
                EventType.ZONE_RECLAIMED, f"ZONE_RECLAIMED:{bar.ts.isoformat()}", bar.ts,
                bar_batch_id=bar.batch_id,
                payload={"entry_eligible_from": eligible_from},
            )
        )
        state = replace(state, zone_lost=False, entry_eligible_from=eligible_from)

    # Trigger: effective from the next expected minute (spec 4.2)
    if (
        signal.trigger_price is not None
        and state.trigger_hit_at is None
        and bar.high >= signal.trigger_price
    ):
        events.append(
            Event(EventType.TRIGGER_HIT, "TRIGGER_HIT", bar.ts, price=signal.trigger_price,
                  bar_batch_id=bar.batch_id)
        )
        state = replace(state, trigger_hit_at=bar.ts)
    return state, events


def _fill(
    state: OrderState, bar: Bar, signal: SignalSpec, ctx: OrderContext, raw_price: Decimal, rule: str
) -> tuple[OrderState, list[Event]]:
    price = _adverse_buy(raw_price, ctx.config.entry_slippage_bps)
    qty = ctx.config.risk_amount / (price - signal.stop)
    cost = _execution_cost(ctx, price, qty, "entry")
    path = EntryPath.RECLAIMED if state.zone_ever_lost else EntryPath.DIRECT
    event = Event(
        EventType.FILLED, "FILLED", bar.ts, price=price, qty=qty, bar_batch_id=bar.batch_id,
        payload={"rule": rule, "entry_path": path, "raw_price": raw_price, "cost": cost},
    )
    state = replace(
        state,
        status=OrderStatus.OPEN,
        avg_entry=price,
        initial_stop=signal.stop,
        stop_current=signal.stop,
        qty_total=qty,
        qty_open=qty,
        costs=state.costs + cost,
        entry_path=path,
        opened_at=bar.ts,
        best_price=price,
    )
    return state, [event]


def _exit_phase(
    state: OrderState, bar: Bar, signal: SignalSpec, ctx: OrderContext, entry_bar: bool
) -> tuple[OrderState, list[Event]]:
    config = ctx.config
    stop_level = state.stop_current
    if state.stop_active_from is not None and bar.ts < state.stop_active_from:
        stop_level = state.stop_previous
    best = state.best_price if entry_bar else _max(state.best_price, bar.high)
    state = replace(state, best_price=best, worst_price=_min(state.worst_price, bar.low))

    # Stop first (spec 4.4)
    if bar.low <= stop_level:
        raw_price = min(bar.open, stop_level)
        price = _adverse_sell(raw_price, config.stop_slippage_bps)
        return _close(
            state, bar, ctx, EventType.STOPPED, price, CloseReason.STOPPED,
            {
                "stop_kind": "BREAKEVEN" if state.t1_done else "INITIAL",
                "stop_level": stop_level,
                "raw_price": raw_price,
            },
        )
    if entry_bar:
        return state, []

    events: list[Event] = []
    if not state.t1_done and bar.high >= signal.target1:
        if signal.target2 is None:
            return _close(
                state, bar, ctx, EventType.TARGET1_HIT, signal.target1,
                CloseReason.TARGET_FINAL, {"final": True},
            )
        qty = state.qty_total * config.target1_scale_out_pct / Decimal(100)
        pnl = (signal.target1 - state.avg_entry) * qty
        cost = _execution_cost(ctx, signal.target1, qty, "exit")
        active_from = ctx.calendar.next_expected_minute(bar.ts)
        events.append(
            Event(
                EventType.TARGET1_HIT, "TARGET1_HIT", bar.ts, price=signal.target1, qty=qty,
                bar_batch_id=bar.batch_id,
                payload={
                    "final": False,
                    "pnl": pnl,
                    "cost": cost,
                    "new_stop_level": state.avg_entry,
                    "stop_active_from": active_from,
                },
            )
        )
        state = replace(
            state,
            status=OrderStatus.PARTIAL,
            t1_done=True,
            qty_open=state.qty_open - qty,
            realized_pnl=state.realized_pnl + pnl,
            costs=state.costs + cost,
            stop_previous=state.stop_current,
            stop_current=state.avg_entry,
            stop_active_from=active_from,
        )
    if state.t1_done and signal.target2 is not None and bar.high >= signal.target2:
        state, closing = _close(
            state, bar, ctx, EventType.TARGET2_HIT, signal.target2,
            CloseReason.TARGET_FINAL, {"final": True},
        )
        events.extend(closing)
    return state, events


def _close(
    state: OrderState,
    bar: Bar,
    ctx: OrderContext,
    event_type: EventType,
    price: Decimal,
    reason: CloseReason,
    extra: dict[str, Any],
) -> tuple[OrderState, list[Event]]:
    qty = state.qty_open
    pnl = (price - state.avg_entry) * qty
    cost = _execution_cost(ctx, price, qty, "exit")
    event = Event(
        event_type, event_type.value, bar.ts, price=price, qty=qty, bar_batch_id=bar.batch_id,
        payload={**extra, "pnl": pnl, "cost": cost},
    )
    state = replace(
        state,
        status=OrderStatus.CLOSED,
        qty_open=ZERO,
        realized_pnl=state.realized_pnl + pnl,
        costs=state.costs + cost,
        close_reason=reason,
        closed_at=bar.ts,
        final_event_ts=bar.ts,
    )
    return state, [event]


def _validity_end(
    state: OrderState, bar: Bar | None, ctx: OrderContext
) -> tuple[OrderState, list[Event]]:
    if state.status is OrderStatus.PENDING:
        event = Event(EventType.EXPIRED, "EXPIRED", payload={"valid_until_ts": ctx.valid_until_ts})
        return replace(state, status=OrderStatus.EXPIRED, final_event_ts=ctx.valid_until_ts), [event]
    assert bar is not None
    price = _adverse_sell(bar.close, ctx.config.stop_slippage_bps)
    return _close(state, bar, ctx, EventType.TIME_EXIT, price, CloseReason.TIME_EXIT,
                  {"raw_price": bar.close})


@_in_v1_context
def apply_validity_end(
    state: OrderState, ctx: OrderContext, last_bar: Bar | None, now: datetime
) -> StepResult:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if now < ctx.valid_until_ts or state.is_final or state.frozen:
        return StepResult(state)
    if state.status is OrderStatus.PENDING:
        new_state, events = _validity_end(state, None, ctx)
        return StepResult(new_state, tuple(events))
    if last_bar is None:
        return flag_review(state, "NO_EXIT_BAR", ctx.valid_until_ts.isoformat())
    if last_bar.ts != state.last_bar_ts:
        raise ValueError("last_bar must be the latest processed bar")

    is_long = ctx.signal.direction is Direction.LONG
    work_state = state if is_long else mirror_state(state)
    work_bar = last_bar if is_long else mirror_bar(last_bar)
    work_state, events = _validity_end(work_state, work_bar, ctx)
    if not is_long:
        work_state = mirror_state(work_state)
        events = [mirror_event(event) for event in events]
    if last_bar.ts != ctx.calendar.last_expected_minute_before(ctx.valid_until_ts):
        review = flag_review(work_state, "STALE_EXIT_BAR", ctx.valid_until_ts.isoformat())
        return StepResult(review.state, tuple(events) + review.events)
    return StepResult(work_state, tuple(events))


@_in_v1_context
def flag_review(state: OrderState, reason: str, ref: str) -> StepResult:
    event = Event(
        EventType.NEEDS_REVIEW, f"NEEDS_REVIEW:{reason}:{ref}",
        payload={"reason": reason, "ref": ref},
    )
    reasons = state.review_reasons if reason in state.review_reasons else state.review_reasons + (reason,)
    return StepResult(replace(state, review_reasons=reasons), (event,))


@_in_v1_context
def cancel(state: OrderState, at: datetime, requested_by: str = "user") -> StepResult:
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    if state.is_final:
        return StepResult(state)
    event = Event(
        EventType.CANCELED, "CANCELED",
        payload={"at": at, "requested_by": requested_by, "open_qty": state.qty_open},
    )
    return StepResult(replace(state, status=OrderStatus.CANCELED, final_event_ts=at), (event,))


@_in_v1_context
def freeze(state: OrderState, reason: str, ref: str) -> StepResult:
    if state.is_final:
        return StepResult(state)
    if state.frozen and reason in state.review_reasons:
        return StepResult(state)
    frozen_event = Event(EventType.FROZEN, f"FROZEN:{reason}", payload={"reason": reason, "ref": ref})
    review = flag_review(replace(state, frozen=True), reason, ref)
    return StepResult(review.state, (frozen_event,) + review.events)


@_in_v1_context
def apply_dividend(
    state: OrderState, ctx: OrderContext, ex_date: date, amount: Decimal, validated: bool
) -> StepResult:
    amount = coerce_decimal(amount, "amount")
    if state.is_final or state.frozen or state.qty_open <= ZERO:
        return StepResult(state)
    ref = ex_date.isoformat()
    if not validated:
        return flag_review(state, "DIVIDEND_UNVERIFIED", ref)
    sign = Decimal(1) if ctx.signal.direction is Direction.LONG else Decimal(-1)
    cash = sign * amount * state.qty_open
    event = Event(
        EventType.DIVIDEND, f"DIVIDEND:{ref}", qty=state.qty_open,
        payload={"ex_date": ex_date, "amount_per_share": amount, "cash": cash},
    )
    return StepResult(replace(state, dividends=state.dividends + cash), (event,))
