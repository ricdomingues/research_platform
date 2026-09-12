from datetime import date

import pytest

from core.domain.models import CloseReason, Direction, EventType, OrderStatus
from core.domain.position import r_multiple
from core.fills.v1 import (
    apply_dividend, apply_validity_end, cancel, flag_review, freeze, new_order_state, run_bars, step,
)
from tests.support import D, bar, et, long_signal, make_ctx

ENTRY = bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101.2)


def types(events):
    return [e.type for e in events]


def one_session_ctx(**signal_overrides):
    return make_ctx(long_signal(valid_sessions=1, **signal_overrides))


def test_time_exit_on_last_expected_minute():
    ctx = one_session_ctx()
    last = bar(et("2025-11-25", "15:59"), 103, 103.5, 102.5, 103)
    result = run_bars(new_order_state(ctx).state, [ENTRY, last], ctx)
    assert types(result.events) == [EventType.FILLED, EventType.TIME_EXIT]
    exit_event = result.events[1]
    assert exit_event.price == D("102.9485") and exit_event.qty == D(25)
    assert exit_event.payload["raw_price"] == D(103)
    assert result.state.close_reason is CloseReason.TIME_EXIT


def test_pending_order_expires_on_last_expected_minute():
    ctx = one_session_ctx()
    result = step(new_order_state(ctx).state, bar(et("2025-11-25", "15:59"), 104, 104, 103.5, 104), ctx)
    (expired,) = result.events
    assert expired.type is EventType.EXPIRED and expired.bar_ts is None
    assert result.state.status is OrderStatus.EXPIRED
    assert result.state.final_event_ts == et("2025-11-25", "16:00")


def test_half_day_last_minute_and_bars_after_validity_ignored():
    ctx = make_ctx(long_signal(valid_sessions=1), start=et("2025-11-28", "09:30"))
    result = step(new_order_state(ctx).state, bar(et("2025-11-28", "12:59"), 104, 104, 103.5, 104), ctx)
    assert types(result.events) == [EventType.EXPIRED]
    later = step(new_order_state(ctx).state, bar(et("2025-12-01", "09:30"), 101, 101, 101, 101), ctx)
    assert later.events == ()


def test_apply_validity_end_when_last_bar_missing():
    ctx = one_session_ctx()
    last_seen = bar(et("2025-11-25", "15:30"), 103, 103.5, 102.5, 103)
    opened = run_bars(new_order_state(ctx).state, [ENTRY, last_seen], ctx)
    assert apply_validity_end(opened.state, ctx, last_seen, et("2025-11-25", "15:45")).events == ()
    closed = apply_validity_end(opened.state, ctx, last_seen, et("2025-11-25", "16:30"))
    assert types(closed.events) == [EventType.TIME_EXIT]
    assert closed.events[0].bar_ts == et("2025-11-25", "15:30")
    with pytest.raises(ValueError):
        apply_validity_end(opened.state, ctx, ENTRY, et("2025-11-25", "16:30"))

    pending = new_order_state(ctx).state
    expired = apply_validity_end(pending, ctx, None, et("2025-11-25", "16:30"))
    assert types(expired.events) == [EventType.EXPIRED]

    no_bar = apply_validity_end(opened.state, ctx, None, et("2025-11-25", "16:30"))
    assert [e.event_key for e in no_bar.events] == [
        "NEEDS_REVIEW:NO_EXIT_BAR:2025-11-25T21:00:00+00:00"
    ]
    assert no_bar.state.review_reasons == ("NO_EXIT_BAR",)


def test_short_time_exit_slippage_is_adverse():
    ctx = make_ctx(long_signal(direction=Direction.SHORT, stop=D(105), target1=D(96),
                               target2=D(92), valid_sessions=1))
    entry = bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101)
    last = bar(et("2025-11-25", "15:59"), 99, 99.5, 98.5, 99)
    result = run_bars(new_order_state(ctx).state, [entry, last], ctx)
    assert result.events[-1].type is EventType.TIME_EXIT
    assert result.events[-1].price == D("99.0495")


def test_cancel():
    ctx = make_ctx()
    state = new_order_state(ctx).state
    canceled = cancel(state, et("2025-11-25", "10:00"))
    assert types(canceled.events) == [EventType.CANCELED]
    assert canceled.state.status is OrderStatus.CANCELED
    assert cancel(canceled.state, et("2025-11-25", "10:01")).events == ()
    assert step(canceled.state, ENTRY, ctx).events == ()


def test_freeze_blocks_market_events_and_flags_review():
    ctx = make_ctx()
    frozen = freeze(new_order_state(ctx).state, "SPLIT", "2025-11-26")
    assert [e.event_key for e in frozen.events] == ["FROZEN:SPLIT", "NEEDS_REVIEW:SPLIT:2025-11-26"]
    assert frozen.state.frozen and frozen.state.review_reasons == ("SPLIT",)
    assert step(frozen.state, ENTRY, ctx).events == ()


def test_freeze_is_idempotent_for_same_reason():
    ctx = make_ctx()
    first = freeze(new_order_state(ctx).state, "SPLIT", "2025-11-26")
    second = freeze(first.state, "SPLIT", "2025-11-26")
    assert second.events == ()
    assert second.state is first.state


def test_freeze_with_new_reason_on_frozen_order_still_flags():
    ctx = make_ctx()
    first = freeze(new_order_state(ctx).state, "SPLIT", "2025-11-26")
    second = freeze(first.state, "INTEGRITY", "evt-1")
    assert [e.event_key for e in second.events] == ["FROZEN:INTEGRITY", "NEEDS_REVIEW:INTEGRITY:evt-1"]
    assert second.state.review_reasons == ("SPLIT", "INTEGRITY")


def test_flag_review_does_not_duplicate_reason():
    state = new_order_state(make_ctx()).state
    once = flag_review(state, "DAILY_RANGE_MISMATCH", "2025-11-25")
    twice = flag_review(once.state, "DAILY_RANGE_MISMATCH", "2025-11-26")
    assert twice.state.review_reasons == ("DAILY_RANGE_MISMATCH",)
    assert twice.events[0].event_key == "NEEDS_REVIEW:DAILY_RANGE_MISMATCH:2025-11-26"


def test_dividends_credit_long_debit_short_and_require_validation():
    ctx = make_ctx()
    opened = step(new_order_state(ctx).state, ENTRY, ctx).state
    credited = apply_dividend(opened, ctx, date(2025, 11, 26), D("0.24"), validated=True)
    (dividend,) = credited.events
    assert dividend.event_key == "DIVIDEND:2025-11-26" and dividend.payload["cash"] == D(6)
    assert r_multiple(credited.state, D(100)) == D("0.06")

    unverified = apply_dividend(opened, ctx, date(2025, 11, 26), D("0.24"), validated=False)
    assert [e.event_key for e in unverified.events] == ["NEEDS_REVIEW:DIVIDEND_UNVERIFIED:2025-11-26"]
    assert unverified.state.dividends == D(0)

    assert apply_dividend(new_order_state(ctx).state, ctx, date(2025, 11, 26), D("0.24"), True).events == ()

    short_ctx = make_ctx(long_signal(direction=Direction.SHORT, stop=D(105), target1=D(96), target2=D(92)))
    short_open = step(new_order_state(short_ctx).state,
                      bar(et("2025-11-25", "09:30"), 101, 101.5, 100.5, 101), short_ctx).state
    debited = apply_dividend(short_open, short_ctx, date(2025, 11, 26), D("0.24"), validated=True)
    assert debited.events[0].payload["cash"] == D(-6)
