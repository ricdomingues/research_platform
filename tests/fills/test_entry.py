from core.domain.hashing import sha256_hex
from core.domain.models import (
    Direction, EntryPath, EventType, FillConfig, GatingState, OrderStatus, ZoneLostPolicy,
)
from core.fills import get_fill_model
from core.fills.v1 import new_order_state, run_bars, step
from tests.support import D, bar, et, flat_bars, long_signal, make_ctx


def start(ctx):
    return new_order_state(ctx).state


def types(events):
    return [e.type for e in events]


def short_signal(**overrides):
    values = dict(direction=Direction.SHORT, entry_zone_low=D(100), entry_zone_high=D(102),
                  stop=D(105), target1=D(96), target2=D(92))
    values.update(overrides)
    return long_signal(**values)


def test_registry_and_order_created_event():
    assert get_fill_model("v1").VERSION == "v1"
    ctx = make_ctx()
    result = new_order_state(ctx)
    assert result.state.status is OrderStatus.PENDING
    assert result.state.stop_current == D(97)
    (created,) = result.events
    assert created.type is EventType.ORDER_CREATED and created.event_key == "ORDER_CREATED"
    assert created.payload["fill_model_version"] == "v1"
    assert created.payload["inherited_signal_state"] is None
    assert created.payload["evaluation_start_ts"] == et("2025-11-25", "09:30")


def test_rule1_open_at_stop_invalidates():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 97, 98, 96, 97.5), ctx)
    assert result.state.status is OrderStatus.INVALIDATED
    assert types(result.events) == [EventType.INVALIDATED]
    assert result.events[0].payload["reason"] == "OPEN_AT_OR_THROUGH_STOP"


def test_rule2_open_inside_zone_fills_at_open():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101.2), ctx)
    (filled,) = result.events
    assert filled.type is EventType.FILLED
    assert filled.price == D(101) and filled.qty == D(25)
    assert filled.payload["rule"] == "ZONE_OPEN"
    assert result.state.status is OrderStatus.OPEN
    assert result.state.entry_path is EntryPath.DIRECT


def test_rule3_open_above_zone_crossing_fills_at_zone_high():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 103, 103.5, 101.5, 102.5), ctx)
    (filled,) = result.events
    assert filled.price == D(102) and filled.qty == D(20)
    assert filled.payload["rule"] == "ZONE_CROSS"


def test_touching_zone_high_does_not_fill():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 103, 103.5, 102, 102.5), ctx)
    assert result.events == ()
    assert result.state.status is OrderStatus.PENDING


def test_ambiguous_candle_without_position_invalidates_never_fills():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 99, 103, 96, 101), ctx)
    assert types(result.events) == [EventType.INVALIDATED]
    assert result.events[0].payload["reason"] == "STOP_TOUCHED_WITHOUT_POSITION"


def test_open_below_zone_loses_zone_without_fill():
    ctx = make_ctx()
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6), ctx)
    assert types(result.events) == [EventType.ZONE_LOST]
    assert result.state.zone_lost and result.state.status is OrderStatus.PENDING
    assert result.events[0].payload["zone_boundary_level"] == D(100)


def test_intrabar_violation_is_not_zone_loss():
    ctx = make_ctx(long_signal(trigger_price=D(104)))
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 103, 103, 99, 101), ctx)
    assert result.events == ()
    assert not result.state.zone_lost


def test_reclaim_blocks_fill_until_next_expected_minute():
    ctx = make_ctx()
    bars = [
        bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6),
        bar(et("2025-11-25", "09:31"), 99.6, 101, 99.5, 100.5),
        bar(et("2025-11-25", "09:32"), 100.5, 101, 100.2, 100.8),
    ]
    result = run_bars(start(ctx), bars, ctx)
    assert types(result.events) == [EventType.ZONE_LOST, EventType.ZONE_RECLAIMED, EventType.FILLED]
    reclaimed, filled = result.events[1], result.events[2]
    assert reclaimed.payload["entry_eligible_from"] == et("2025-11-25", "09:32")
    assert filled.bar_ts == et("2025-11-25", "09:32") and filled.price == D("100.5")
    assert result.state.entry_path is EntryPath.RECLAIMED


def test_zone_lost_and_reclaimed_in_same_candle():
    ctx = make_ctx()
    first = step(start(ctx), bar(et("2025-11-25", "09:30"), 99.5, 101, 99.2, 100.4), ctx)
    assert types(first.events) == [EventType.ZONE_LOST, EventType.ZONE_RECLAIMED]
    second = step(first.state, bar(et("2025-11-25", "09:31"), 100.4, 100.9, 100.1, 100.6), ctx)
    assert types(second.events) == [EventType.FILLED]


def test_reclaim_on_last_minute_waits_for_next_session():
    ctx = make_ctx()
    bars = [
        bar(et("2025-11-25", "15:58"), 99.5, 99.8, 99, 99.6),
        bar(et("2025-11-25", "15:59"), 99.6, 101, 99.5, 100.5),
    ]
    result = run_bars(start(ctx), bars, ctx)
    assert result.state.entry_eligible_from == et("2025-11-26", "09:30")


def test_zone_lost_cancel_policy_invalidates():
    ctx = make_ctx(config=FillConfig(zone_lost_policy=ZoneLostPolicy.CANCEL))
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6), ctx)
    assert types(result.events) == [EventType.ZONE_LOST, EventType.INVALIDATED]
    assert result.events[1].payload["reason"] == "ZONE_LOST_CANCEL"


def test_trigger_hit_takes_effect_on_next_expected_minute():
    ctx = make_ctx(long_signal(trigger_price=D("101.5")))
    first = step(start(ctx), bar(et("2025-11-25", "09:30"), 101, 101.6, 100.8, 101.2), ctx)
    assert types(first.events) == [EventType.TRIGGER_HIT]
    second = step(first.state, bar(et("2025-11-25", "09:31"), 101.2, 101.4, 101, 101.3), ctx)
    assert types(second.events) == [EventType.FILLED]
    assert second.events[0].price == D("101.2")


def test_no_market_event_before_evaluation_start():
    ctx = make_ctx(start=et("2025-11-25", "11:30"))
    early = flat_bars(ctx.calendar, et("2025-11-25", "10:00"), et("2025-11-25", "11:30"), 101)
    result = run_bars(start(ctx), early + [bar(et("2025-11-25", "11:30"), 101, 101, 101, 101)], ctx)
    assert types(result.events) == [EventType.FILLED]
    assert result.events[0].bar_ts == et("2025-11-25", "11:30")


def test_entry_slippage_is_adverse():
    ctx = make_ctx(config=FillConfig(entry_slippage_bps=D(10)))
    result = step(start(ctx), bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101.2), ctx)
    assert result.events[0].price == D("101.101")
    assert result.events[0].payload["raw_price"] == D(101)


def test_short_entry_rules_are_mirrored():
    ctx = make_ctx(short_signal())
    inside = step(start(ctx), bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101), ctx)
    assert inside.events[0].price == D(101) and inside.events[0].qty == D(25)
    crossing = step(start(ctx), bar(et("2025-11-25", "09:30"), 99, 100.5, 98.8, 100.2), ctx)
    assert crossing.events[0].price == D(100) and crossing.events[0].qty == D(20)
    assert crossing.state.avg_entry == D(100) and crossing.state.stop_current == D(105)
    through_stop = step(start(ctx), bar(et("2025-11-25", "09:30"), 105, 105.5, 104, 104.5), ctx)
    assert through_stop.state.status is OrderStatus.INVALIDATED
    lost = step(start(ctx), bar(et("2025-11-25", "09:30"), 103, 103.5, 102.5, 103), ctx)
    assert types(lost.events) == [EventType.ZONE_LOST]
    assert lost.events[0].payload["zone_boundary_level"] == D(102)


def test_ignores_non_expected_minutes_and_reprocessed_bars():
    ctx = make_ctx()
    state = start(ctx)
    assert step(state, bar(et("2025-11-25", "16:00"), 101, 101, 101, 101), ctx).events == ()
    first = step(state, bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6), ctx)
    again = step(first.state, bar(et("2025-11-25", "09:30"), 99.5, 99.8, 99, 99.6), ctx)
    assert again.events == () and again.state == first.state


def test_inherited_reclaimed_state_marks_entry_path_reclaimed():
    ctx = make_ctx()
    inherited = GatingState(OrderStatus.PENDING, zone_lost=False, entry_eligible_from=et("2025-11-25", "09:31"), trigger_hit_at=None)
    result = new_order_state(ctx, inherited=inherited)
    assert result.state.zone_ever_lost
    state = step(result.state, bar(et("2025-11-25", "09:31"), 101, 101.5, 100.5, 101.2), ctx)
    assert types(state.events) == [EventType.FILLED]
    assert state.events[0].payload["entry_path"] is EntryPath.RECLAIMED


def test_inherited_state_is_copied_hashed_and_must_be_pending():
    ctx = make_ctx()
    gating = GatingState(OrderStatus.PENDING, zone_lost=True, entry_eligible_from=None, trigger_hit_at=et("2025-11-25", "09:30"))
    result = new_order_state(ctx, inherited=gating)
    assert result.state.zone_lost and result.state.trigger_hit_at == et("2025-11-25", "09:30")
    assert result.events[0].payload["inherited_signal_state"] == gating.as_payload()
    assert result.events[0].payload["inherited_signal_state_hash"] == sha256_hex(gating.as_payload())

    non_pending = GatingState(OrderStatus.OPEN, zone_lost=False, entry_eligible_from=None, trigger_hit_at=None)
    try:
        new_order_state(ctx, inherited=non_pending)
        assert False, "should raise ValueError"
    except ValueError as e:
        assert "inherited signal state must be PENDING" in str(e)


def test_extra_payload_cannot_override_reserved_keys():
    ctx = make_ctx()
    try:
        new_order_state(ctx, extra_payload={"signal": 1})
        assert False, "should raise ValueError"
    except ValueError as e:
        assert "extra_payload overrides reserved keys" in str(e)

    result = new_order_state(ctx, extra_payload={"actionability_run_id": "r1"})
    assert result.events[0].payload["actionability_run_id"] == "r1"

    try:
        new_order_state(ctx, extra_payload={"calendar_sessions_hash": "x"})
        assert False, "should raise ValueError"
    except ValueError as e:
        assert "calendar_sessions_hash" in str(e)


def test_order_created_records_calendar_sessions_hash():
    from core.domain.calendar import calendar_window_hash

    ctx = make_ctx()
    (created,) = new_order_state(ctx).events
    assert created.payload["calendar_sessions_hash"] == calendar_window_hash(
        ctx.calendar, ctx.evaluation_start_ts, ctx.valid_until_ts
    )
    assert len(created.payload["calendar_sessions_hash"]) == 64


def test_et_aware_bar_produces_same_event_key_and_hash_as_utc_bar():
    from datetime import timezone
    from zoneinfo import ZoneInfo

    ctx = make_ctx()
    utc_bar = bar(et("2025-11-25", "09:31"), 99.5, 99.8, 99, 99.6)
    et_bar = bar(utc_bar.ts.astimezone(ZoneInfo("America/New_York")), 99.5, 99.8, 99, 99.6)
    (utc_event,) = step(start(ctx), utc_bar, ctx).events
    (et_event,) = step(start(ctx), et_bar, ctx).events
    assert et_event.type is EventType.ZONE_LOST
    assert et_event.event_key == utc_event.event_key == "ZONE_LOST:2025-11-25T14:31:00+00:00"
    assert et_event.payload_hash == utc_event.payload_hash
    assert et_event.bar_ts.tzinfo is timezone.utc
    assert et_bar.ts.tzinfo is timezone.utc
