import pytest

from core.actionability import (
    ActionabilityReason, ManualOrderRejected, build_manual_order, signal_actionability,
)
from core.domain.hashing import sha256_hex
from core.domain.models import EventType, OrderStatus
from core.fills import get_fill_model
from core.fills.v1 import run_bars
from tests.support import D, bar, et, flat_bars, long_signal, make_ctx

V1 = get_fill_model("v1")
DAY = "2025-11-25"


def signal_ctx(**overrides):
    return make_ctx(long_signal(**overrides), start=et(DAY, "09:40"))


def flat(ctx, start, end, price):
    return flat_bars(ctx.calendar, et(DAY, start), et(DAY, end), price)


def entry_at_1005(ctx):
    return flat(ctx, "09:40", "10:05", 105) + [bar(et(DAY, "10:05"), 101, 101.5, 100.5, 101.2)]


def test_stop_touched_before_click_is_not_actionable():
    ctx = signal_ctx()
    bars = flat(ctx, "09:40", "10:20", 105) + [bar(et(DAY, "10:20"), 98, 98.5, 96, 98)]
    decision = signal_actionability(V1, ctx, bars, et(DAY, "13:00"))
    assert decision.reason is ActionabilityReason.INVALIDATED
    assert not decision.actionable
    assert decision.error_code == "SIGNAL_NO_LONGER_ACTIONABLE"
    with pytest.raises(ManualOrderRejected) as rejected:
        build_manual_order(V1, ctx, bars, et(DAY, "13:00"))
    assert rejected.value.actionability.reason is ActionabilityReason.INVALIDATED


def test_prior_hypothetical_entry_blocks_manual_order():
    ctx = signal_ctx()
    bars = (
        entry_at_1005(ctx)
        + flat(ctx, "10:06", "11:00", 103)
        + [bar(et(DAY, "11:00"), 105, 106, 104.8, 105.5)]
        + flat(ctx, "11:01", "13:00", 104)
    )
    decision = signal_actionability(V1, ctx, bars, et(DAY, "13:00"))
    assert decision.reason is ActionabilityReason.ENTRY_OPPORTUNITY_ALREADY_OCCURRED
    assert decision.hypothetical_status is OrderStatus.PARTIAL


def test_stopped_and_target_reached_reasons():
    ctx = signal_ctx()
    stopped = entry_at_1005(ctx) + [bar(et(DAY, "10:30"), 100, 100.2, 96.5, 97)]
    assert signal_actionability(V1, ctx, stopped, et(DAY, "13:00")).reason is ActionabilityReason.STOPPED
    reached = (
        entry_at_1005(ctx)
        + [bar(et(DAY, "11:00"), 105, 106, 104.8, 105.5)]
        + [bar(et(DAY, "11:30"), 109, 110.5, 108.8, 110)]
    )
    decision = signal_actionability(V1, ctx, reached, et(DAY, "13:00"))
    assert decision.reason is ActionabilityReason.TARGET_REACHED


def test_expired_signal():
    ctx = signal_ctx()
    decision = signal_actionability(V1, ctx, [], et("2025-11-28", "13:00"))
    assert decision.reason is ActionabilityReason.SIGNAL_EXPIRED
    assert decision.error_code == "SIGNAL_EXPIRED"
    with pytest.raises(ManualOrderRejected) as rejected:
        build_manual_order(V1, ctx, [], et("2025-11-28", "12:59", 30))
    assert rejected.value.actionability.reason is ActionabilityReason.SIGNAL_EXPIRED


def test_manual_order_inherits_zone_lost_and_waits_for_reclaim():
    ctx = signal_ctx()
    history = [bar(et(DAY, "10:00"), 99.5, 99.8, 99, 99.6)] + flat(ctx, "10:01", "10:30", 99.5)
    decision = signal_actionability(V1, ctx, history, et(DAY, "10:30"))
    assert decision.actionable and decision.gating_state.zone_lost

    manual = build_manual_order(V1, ctx, history, et(DAY, "10:30"),
                                extra_payload={"actionability_run_id": "run-1"})
    assert manual.context.evaluation_start_ts == et(DAY, "10:30")
    assert manual.context.valid_until_ts == ctx.valid_until_ts
    (created,) = manual.created.events
    inherited = created.payload["inherited_signal_state"]
    assert inherited["zone_lost"] is True and inherited["status"] is OrderStatus.PENDING
    assert created.payload["inherited_signal_state_hash"] == sha256_hex(inherited)
    assert created.payload["actionability_run_id"] == "run-1"

    later = [
        bar(et(DAY, "10:30"), 99.6, 101, 99.5, 100.5),
        bar(et(DAY, "10:31"), 100.5, 101, 100.2, 100.8),
    ]
    result = run_bars(manual.created.state, history + later, manual.context)
    assert [e.type for e in result.events] == [EventType.ZONE_RECLAIMED, EventType.FILLED]
    assert result.events[1].bar_ts == et(DAY, "10:31")


def test_trigger_only_counts_from_complete_candles_before_click():
    ctx = signal_ctx(trigger_price=D("101.5"))
    history = [bar(et(DAY, "09:45"), 101, 101.6, 100.8, 101.2)]
    too_early = signal_actionability(V1, ctx, history, et(DAY, "09:45", 30))
    assert too_early.actionable and too_early.gating_state.trigger_hit_at is None
    complete = signal_actionability(V1, ctx, history, et(DAY, "09:46"))
    assert complete.gating_state.trigger_hit_at == et(DAY, "09:45")


def test_manual_order_from_morning_signal_has_no_events_before_click():
    ctx = make_ctx(long_signal(), start=et(DAY, "08:00"))
    history = flat(ctx, "09:30", "14:00", 105)
    manual = build_manual_order(V1, ctx, history, et(DAY, "14:00"))
    assert manual.context.evaluation_start_ts == et(DAY, "14:00")
    assert manual.context.valid_until_ts == ctx.valid_until_ts
    entry = bar(et(DAY, "14:00"), 101, 101.5, 100.5, 101.2)
    result = run_bars(manual.created.state, history + [entry], manual.context)
    assert [e.bar_ts for e in result.events] == [et(DAY, "14:00")]


def test_actionability_is_pure():
    ctx = signal_ctx()
    bars = entry_at_1005(ctx)
    first = signal_actionability(V1, ctx, bars, et(DAY, "12:00"))
    second = signal_actionability(V1, ctx, list(reversed(bars)), et(DAY, "12:00"))
    assert first == second
