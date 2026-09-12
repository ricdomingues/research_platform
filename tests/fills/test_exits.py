from core.domain.models import CloseReason, Direction, EventType, FillConfig, OrderStatus
from core.domain.position import excursion_r, r_multiple
from core.fills.v1 import new_order_state, run_bars, step
from tests.support import D, bar, et, long_signal, make_ctx

ENTRY = bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101.2)


def opened(ctx, entry=ENTRY):
    return step(new_order_state(ctx).state, entry, ctx)


def types(events):
    return [e.type for e in events]


def test_fill_and_stop_in_same_candle_is_worst_case():
    ctx = make_ctx()
    result = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 101.5, 96.5, 97.5))
    assert types(result.events) == [EventType.FILLED, EventType.STOPPED]
    stopped = result.events[1]
    assert stopped.price == D("96.9515")
    assert stopped.payload["stop_kind"] == "INITIAL"
    assert stopped.payload["pnl"] == D("-101.2125")
    assert result.state.status is OrderStatus.CLOSED
    assert result.state.close_reason is CloseReason.STOPPED


def test_entry_candle_never_hits_target():
    ctx = make_ctx()
    first = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 107, 100.5, 106.5))
    assert types(first.events) == [EventType.FILLED]
    second = step(first.state, bar(et("2025-11-25", "09:31"), 106, 106.2, 105.5, 106), ctx)
    assert types(second.events) == [EventType.TARGET1_HIT]


def test_target_by_touch_scales_out_and_schedules_breakeven():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 105, 106, 104.8, 105.5), ctx)
    (t1,) = result.events
    assert t1.type is EventType.TARGET1_HIT
    assert t1.price == D(106) and t1.qty == D("12.5") and t1.payload["pnl"] == D("62.5")
    state = result.state
    assert state.status is OrderStatus.PARTIAL and state.qty_open == D("12.5")
    assert state.stop_current == D(101) and state.stop_previous == D(97)
    assert state.stop_active_from == et("2025-11-25", "09:32")


def test_breakeven_not_active_on_target1_candle_then_active():
    ctx = make_ctx()
    t1 = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 105, 106, 100, 100.5), ctx)
    assert types(t1.events) == [EventType.TARGET1_HIT]
    after = step(t1.state, bar(et("2025-11-25", "09:32"), 101.5, 101.6, 100.9, 101), ctx)
    (stopped,) = after.events
    assert stopped.type is EventType.STOPPED
    assert stopped.payload["stop_kind"] == "BREAKEVEN"
    assert stopped.price == D("100.9495") and stopped.qty == D("12.5")


def test_stop_wins_over_target_in_same_candle():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 100, 106.5, 96, 99), ctx)
    assert types(result.events) == [EventType.STOPPED]
    assert result.events[0].payload["raw_price"] == D(97)


def test_gap_through_stop_fills_at_open_with_slippage():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 95, 95.5, 94, 95), ctx)
    assert result.events[0].price == D("94.9525")


def test_gap_above_target_gets_no_price_improvement():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 108, 108.5, 107, 108), ctx)
    assert types(result.events) == [EventType.TARGET1_HIT]
    assert result.events[0].price == D(106)


def test_target1_and_target2_in_same_candle():
    ctx = make_ctx()
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 105, 111, 104, 110.5), ctx)
    assert types(result.events) == [EventType.TARGET1_HIT, EventType.TARGET2_HIT]
    assert result.events[1].price == D(110) and result.events[1].qty == D("12.5")
    assert result.state.close_reason is CloseReason.TARGET_FINAL
    assert r_multiple(result.state, D(100)) == D("1.75")


def test_without_target2_target1_closes_everything():
    ctx = make_ctx(long_signal(target2=None))
    result = step(opened(ctx).state, bar(et("2025-11-25", "09:31"), 105, 106, 104.8, 105.5), ctx)
    (t1,) = result.events
    assert t1.qty == D(25) and t1.payload["final"] is True
    assert result.state.status is OrderStatus.CLOSED
    assert result.state.close_reason is CloseReason.TARGET_FINAL


def test_commission_charged_per_execution():
    ctx = make_ctx(config=FillConfig(commission_per_execution=D(1)))
    bars = [
        ENTRY,
        bar(et("2025-11-25", "09:31"), 105, 106, 104.8, 105.5),
        bar(et("2025-11-25", "09:32"), 101.5, 101.6, 100.9, 101),
    ]
    result = run_bars(new_order_state(ctx).state, bars, ctx)
    assert types(result.events) == [EventType.FILLED, EventType.TARGET1_HIT, EventType.STOPPED]
    assert result.state.costs == D(3)
    assert all(e.payload["cost"] == D(1) for e in result.events)


def test_sec_taf_fees_apply_to_sells_only():
    config = FillConfig(sec_taf_fees_enabled=True, sec_fee_rate=D("0.0001"),
                        taf_fee_per_share=D("0.01"), taf_fee_max=D(5))
    long_ctx = make_ctx(config=config)
    first = opened(long_ctx)
    assert first.events[0].payload["cost"] == D(0)
    t1 = step(first.state, bar(et("2025-11-25", "09:31"), 105, 106, 104.8, 105.5), long_ctx)
    assert t1.events[0].payload["cost"] == D("0.2575")
    short_ctx = make_ctx(
        long_signal(direction=Direction.SHORT, stop=D(105), target1=D(96), target2=D(92)), config
    )
    short_fill = opened(short_ctx, bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101))
    assert short_fill.events[0].payload["cost"] == D("0.5025")


def test_excursions_ignore_entry_candle_high():
    ctx = make_ctx()
    first = opened(ctx)
    assert first.state.best_price == D(101) and first.state.worst_price == D("100.5")
    second = step(first.state, bar(et("2025-11-25", "09:31"), 101.2, 104, 99.5, 103), ctx)
    assert excursion_r(second.state, Direction.LONG) == (D("0.75"), D("-0.375"))


def test_stop_candle_high_does_not_improve_mfe_long():
    ctx = make_ctx()
    first = opened(ctx)
    assert first.state.best_price == D(101)
    stopped = step(first.state, bar(et("2025-11-25", "09:31"), 100, 106.5, 96, 99), ctx)
    assert types(stopped.events) == [EventType.STOPPED]
    assert stopped.state.best_price == D(101)
    assert stopped.state.worst_price == D(96)
    entry_stop = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 104, 96.5, 97.5))
    assert types(entry_stop.events) == [EventType.FILLED, EventType.STOPPED]
    assert entry_stop.state.best_price == D(101) and entry_stop.state.worst_price == D("96.5")
    # Ordinary and target candles still update MFE with their high.
    ordinary = step(first.state, bar(et("2025-11-25", "09:31"), 101.2, 104, 99.5, 103), ctx)
    assert ordinary.state.best_price == D(104)
    target = step(first.state, bar(et("2025-11-25", "09:31"), 105, 106.4, 104.8, 105.5), ctx)
    assert types(target.events) == [EventType.TARGET1_HIT] and target.state.best_price == D("106.4")


def test_stop_candle_low_does_not_improve_mfe_short():
    ctx = make_ctx(long_signal(direction=Direction.SHORT, stop=D(105), target1=D(96), target2=D(92)))
    first = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101))
    stopped = step(first.state, bar(et("2025-11-25", "09:31"), 102, 106, 95.5, 103), ctx)
    assert types(stopped.events) == [EventType.STOPPED]
    assert stopped.state.best_price == D(101)
    assert stopped.state.worst_price == D(106)
    assert excursion_r(stopped.state, Direction.SHORT) == (D(0), D("-1.25"))
    entry_stop = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 105.5, 98, 104))
    assert types(entry_stop.events) == [EventType.FILLED, EventType.STOPPED]
    assert entry_stop.state.best_price == D(101) and entry_stop.state.worst_price == D("105.5")
    ordinary = step(first.state, bar(et("2025-11-25", "09:31"), 100.5, 101, 98, 99), ctx)
    assert ordinary.state.best_price == D(98)


def test_short_exits_are_mirrored():
    ctx = make_ctx(long_signal(direction=Direction.SHORT, stop=D(105), target1=D(96), target2=D(92)))
    first = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101))
    t1 = step(first.state, bar(et("2025-11-25", "09:31"), 97, 97.2, 96, 96.5), ctx)
    assert types(t1.events) == [EventType.TARGET1_HIT]
    assert t1.events[0].price == D(96) and t1.events[0].payload["pnl"] == D("62.5")
    assert t1.state.stop_current == D(101)
    assert t1.events[0].payload["new_stop_level"] == D(101)
    stop = step(t1.state, bar(et("2025-11-25", "09:32"), 100, 101.2, 99.8, 101), ctx)
    (stopped,) = stop.events
    assert stopped.payload["stop_kind"] == "BREAKEVEN"
    assert stopped.price == D("101.0505") and stopped.payload["stop_level"] == D(101)
    assert stopped.payload["pnl"] == D("-0.63125")


def test_zone_cross_fill_and_stop_in_same_candle():
    ctx = make_ctx()
    result = opened(ctx, bar(et("2025-11-25", "09:30"), 103, 103.5, 96, 98))
    assert types(result.events) == [EventType.FILLED, EventType.STOPPED]
    filled, stopped = result.events
    assert filled.price == D(102) and filled.qty == D(20)
    assert stopped.payload["raw_price"] == D(97)
    assert stopped.price == D("96.9515")
    assert stopped.payload["pnl"] == D("-100.97")
    assert result.state.status is OrderStatus.CLOSED


SHORT_SIGNAL = dict(direction=Direction.SHORT, stop=D(105), target1=D(96), target2=D(92))


def test_short_exit_buy_pays_no_sec_taf_fee():
    config = FillConfig(commission_per_execution=D(1), sec_taf_fees_enabled=True,
                        sec_fee_rate=D("0.0001"), taf_fee_per_share=D("0.01"), taf_fee_max=D(5))
    ctx = make_ctx(long_signal(**SHORT_SIGNAL), config)
    first = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101))
    assert first.events[0].payload["cost"] == D("1.5025")
    result = step(first.state, bar(et("2025-11-25", "09:31"), 104, 105.5, 103.5, 105), ctx)
    (stopped,) = result.events
    assert stopped.type is EventType.STOPPED
    assert stopped.payload["cost"] == D(1)
    assert result.state.costs == D("2.5025")


def test_short_gap_through_stop_fills_at_open_with_slippage():
    ctx = make_ctx(long_signal(**SHORT_SIGNAL))
    first = opened(ctx, bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101))
    result = step(first.state, bar(et("2025-11-25", "09:31"), 107, 107.5, 106.5, 107), ctx)
    (stopped,) = result.events
    assert stopped.type is EventType.STOPPED
    assert stopped.payload["raw_price"] == D(107)
    assert stopped.price == D("107.0535")
    assert result.state.status is OrderStatus.CLOSED
