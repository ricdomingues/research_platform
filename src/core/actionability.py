"""Signal actionability for MANUAL_USER orders (spec 3.4.1). Pure: bars are injected."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import ModuleType
from typing import Any, Iterable, Mapping

from core.domain.calendar import ONE_MINUTE, evaluation_start_ts
from core.domain.models import Bar, CloseReason, GatingState, OrderContext, OrderStatus, StepResult


class ActionabilityReason(StrEnum):
    ACTIONABLE = "ACTIONABLE"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
    INVALIDATED = "INVALIDATED"
    ENTRY_OPPORTUNITY_ALREADY_OCCURRED = "ENTRY_OPPORTUNITY_ALREADY_OCCURRED"
    STOPPED = "STOPPED"
    TARGET_REACHED = "TARGET_REACHED"


@dataclass(frozen=True)
class Actionability:
    reason: ActionabilityReason
    hypothetical_status: OrderStatus
    gating_state: GatingState | None

    @property
    def actionable(self) -> bool:
        return self.reason is ActionabilityReason.ACTIONABLE

    @property
    def error_code(self) -> str | None:
        if self.actionable:
            return None
        if self.reason is ActionabilityReason.SIGNAL_EXPIRED:
            return "SIGNAL_EXPIRED"
        return "SIGNAL_NO_LONGER_ACTIONABLE"


_EXPIRED = Actionability(ActionabilityReason.SIGNAL_EXPIRED, OrderStatus.EXPIRED, None)

_CLOSE_REASONS = {
    CloseReason.STOPPED: ActionabilityReason.STOPPED,
    CloseReason.TARGET_FINAL: ActionabilityReason.TARGET_REACHED,
    CloseReason.TIME_EXIT: ActionabilityReason.SIGNAL_EXPIRED,
}


def signal_actionability(
    fill_model: ModuleType, signal_ctx: OrderContext, bars: Iterable[Bar], as_of: datetime
) -> Actionability:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if as_of >= signal_ctx.valid_until_ts:
        return _EXPIRED

    state = fill_model.new_order_state(signal_ctx).state
    for current in sorted(bars, key=lambda b: b.ts):
        if current.ts + ONE_MINUTE > as_of:
            break
        state = fill_model.step(state, current, signal_ctx).state

    status = state.status
    if status is OrderStatus.PENDING:
        return Actionability(ActionabilityReason.ACTIONABLE, status, state.gating_state())
    if status is OrderStatus.EXPIRED:
        return _EXPIRED
    if status is OrderStatus.INVALIDATED:
        return Actionability(ActionabilityReason.INVALIDATED, status, None)
    if status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
        return Actionability(ActionabilityReason.ENTRY_OPPORTUNITY_ALREADY_OCCURRED, status, None)
    if status is OrderStatus.CLOSED and state.close_reason is not None:
        return Actionability(_CLOSE_REASONS[state.close_reason], status, None)
    raise AssertionError(f"unexpected hypothetical status: {status}")


class ManualOrderRejected(Exception):
    def __init__(self, actionability: Actionability) -> None:
        super().__init__(actionability.error_code)
        self.actionability = actionability


@dataclass(frozen=True)
class ManualOrder:
    context: OrderContext
    created: StepResult
    actionability: Actionability


def build_manual_order(
    fill_model: ModuleType,
    signal_ctx: OrderContext,
    bars: Iterable[Bar],
    created_at: datetime,
    extra_payload: Mapping[str, Any] | None = None,
) -> ManualOrder:
    decision = signal_actionability(fill_model, signal_ctx, bars, created_at)
    if not decision.actionable:
        raise ManualOrderRejected(decision)
    start = evaluation_start_ts(signal_ctx.calendar, created_at)
    if start >= signal_ctx.valid_until_ts:
        raise ManualOrderRejected(_EXPIRED)
    context = OrderContext(
        signal=signal_ctx.signal,
        config=signal_ctx.config,
        calendar=signal_ctx.calendar,
        evaluation_start_ts=start,
        valid_until_ts=signal_ctx.valid_until_ts,
    )
    created = fill_model.new_order_state(
        context, inherited=decision.gating_state, extra_payload=extra_payload
    )
    return ManualOrder(context, created, decision)
