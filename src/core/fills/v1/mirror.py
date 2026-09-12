"""SHORT orders run through the LONG rules on negated prices (p -> -p)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from core.domain.models import Bar, Event, OrderState, SignalSpec

PRICE_SUFFIXES = ("_price", "_level")


def _neg(value: Decimal | None) -> Decimal | None:
    return None if value is None else -value


def mirror_bar(bar: Bar) -> Bar:
    return replace(bar, open=-bar.open, high=-bar.low, low=-bar.high, close=-bar.close)


def mirror_signal(signal: SignalSpec) -> SignalSpec:
    return replace(
        signal,
        entry_zone_low=-signal.entry_zone_high,
        entry_zone_high=-signal.entry_zone_low,
        stop=-signal.stop,
        target1=-signal.target1,
        target2=_neg(signal.target2),
        trigger_price=_neg(signal.trigger_price),
    )


def mirror_state(state: OrderState) -> OrderState:
    return replace(
        state,
        avg_entry=_neg(state.avg_entry),
        initial_stop=_neg(state.initial_stop),
        stop_current=_neg(state.stop_current),
        stop_previous=_neg(state.stop_previous),
        best_price=_neg(state.best_price),
        worst_price=_neg(state.worst_price),
    )


def mirror_event(event: Event) -> Event:
    payload = {
        key: (-value if key.endswith(PRICE_SUFFIXES) and isinstance(value, Decimal) else value)
        for key, value in event.payload.items()
    }
    return replace(event, price=_neg(event.price), payload=payload)
