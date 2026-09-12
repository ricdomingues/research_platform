"""Position results normalized in R (spec 3.7)."""

from __future__ import annotations

from decimal import Decimal, localcontext

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Direction, OrderState


def r_multiple(state: OrderState, risk_amount: Decimal) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        return (state.realized_pnl - state.costs + state.dividends) / risk_amount


def excursion_r(state: OrderState, direction: Direction) -> tuple[Decimal | None, Decimal | None]:
    if state.avg_entry is None or state.initial_stop is None:
        return None, None
    with localcontext(CANONICAL_CONTEXT):
        risk_per_share = abs(state.avg_entry - state.initial_stop)
        sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
        mfe = None if state.best_price is None else sign * (state.best_price - state.avg_entry) / risk_per_share
        mae = None if state.worst_price is None else sign * (state.worst_price - state.avg_entry) / risk_per_share
    return mfe, mae
