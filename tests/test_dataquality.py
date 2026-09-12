from dataclasses import replace
from datetime import date

from core.dataquality import (
    ReviewFlag, active_levels, daily_range_mismatch, dividends_agree, find_gaps, gap_events,
    missing_bar_reviews, quality_window, session_quality,
)
from core.domain.models import EventType, OrderState, OrderStatus
from tests.support import D, bar, et, make_ctx

CTX = make_ctx(start=et("2025-11-25", "09:30"))
CAL = CTX.calendar


def test_quality_window_rules():
    open_state = OrderState(status=OrderStatus.OPEN)
    assert quality_window(open_state, CTX, et("2025-11-25", "10:05", 30)) == (
        et("2025-11-25", "09:30"), et("2025-11-25", "10:05"),
    )
    stopped = OrderState(status=OrderStatus.CLOSED, final_event_ts=et("2025-11-25", "11:00"))
    assert quality_window(stopped, CTX, et("2025-11-26", "12:00"))[1] == et("2025-11-25", "11:01")
    expired = OrderState(status=OrderStatus.EXPIRED, final_event_ts=CTX.valid_until_ts)
    assert quality_window(expired, CTX, et("2025-12-02", "12:00"))[1] == CTX.valid_until_ts


def test_session_quality_per_session_and_event():
    present = {et("2025-11-26", "15:58"), et("2025-11-28", "09:31")}
    report = session_quality(CAL, et("2025-11-26", "15:58"), et("2025-11-28", "09:32"), present)
    assert [(q.day, q.expected, q.missing) for q in report] == [
        (date(2025, 11, 26), 2, (et("2025-11-26", "15:59"),)),
        (date(2025, 11, 28), 2, (et("2025-11-28", "09:30"),)),
    ]
    event = report[0].event()
    assert event.type is EventType.DATA_QUALITY and event.event_key == "DATA_QUALITY:2025-11-26"
    assert event.payload["coverage_pct"] == D("50.00")
    assert event.payload["missing_bars"] == 1 and event.payload["expected_bars"] == 2


def test_find_gaps_follows_expected_minutes_across_sessions():
    same_day = CAL.expected_minutes(et("2025-11-25", "10:00"), et("2025-11-25", "10:30"))
    lone = [et("2025-11-25", "11:00")]
    assert find_gaps(CAL, same_day + lone, 30) == [(et("2025-11-25", "10:00"), 30)]
    across = CAL.expected_minutes(et("2025-11-26", "15:45"), et("2025-11-28", "09:45"))
    assert find_gaps(CAL, across, 30) == [(et("2025-11-26", "15:45"), 30)]
    assert find_gaps(CAL, across, 31) == []
    (gap,) = gap_events([(et("2025-11-26", "15:45"), 30)])
    assert gap.event_key == "DATA_GAP:2025-11-26T20:45:00+00:00" and gap.payload["minutes"] == 30


def test_missing_bar_reviews_touch_absent_and_clear():
    state = OrderState(
        status=OrderStatus.PARTIAL, stop_current=D(101), stop_previous=D(97),
        stop_active_from=et("2025-11-25", "10:02"),
    )
    touch_old_stop = et("2025-11-25", "10:01")
    clear = et("2025-11-25", "10:03")
    absent = et("2025-11-25", "10:04")
    reference = {
        touch_old_stop: bar(touch_old_stop, 98, 98.5, 96.8, 98),
        clear: bar(clear, 103, 104, 102.5, 103.5),
    }
    flags = missing_bar_reviews([absent, clear, touch_old_stop], reference, CTX.signal, state)
    assert flags == [
        ReviewFlag("MISSING_BAR_LEVEL_TOUCH", touch_old_stop.isoformat()),
        ReviewFlag("MISSING_BAR_UNVERIFIABLE", absent.isoformat()),
    ]
    assert D(97) in active_levels(CTX.signal, state, touch_old_stop)
    assert D(101) in active_levels(CTX.signal, state, clear)
    pending = replace(state, status=OrderStatus.PENDING, stop_current=None, stop_previous=None,
                      stop_active_from=None)
    assert D(97) in active_levels(CTX.signal, pending, clear)


def test_daily_range_mismatch_and_dividend_agreement():
    bars = [bar(et("2025-11-25", "09:30"), 100, 101, 99.5, 100.5), bar(et("2025-11-25", "09:31"), 100.5, 102, 100, 101)]
    assert not daily_range_mismatch(bars, D("102.3"), D("99.4"), D("0.5"))
    assert daily_range_mismatch(bars, D("103"), D("99.5"), D("0.5"))
    assert not daily_range_mismatch([], D(1), D(1), D("0.5"))
    assert dividends_agree(D("0.24"), D("0.2405"), D("0.001"))
    assert not dividends_agree(D("0.24"), D("0.25"), D("0.001"))
    assert not dividends_agree(D("0.24"), None, D("0.001"))


def test_dividends_agree_ignores_ambient_decimal_context():
    from decimal import Context, localcontext

    # |1.0015 - 0| = 1.0015 > 1.001, but prec=3 would round it to 1.00 and agree.
    assert not dividends_agree(D("1.0015"), D("0"), D("1.001"))
    with localcontext(Context(prec=3)):
        assert not dividends_agree(D("1.0015"), D("0"), D("1.001"))


def test_session_quality_raises_runtime_error_when_calendar_is_inconsistent():
    import pytest

    from core.domain.calendar import SessionCalendar

    class Broken(SessionCalendar):
        def session_containing(self, ts):
            return None

    broken = Broken(CAL.sessions)
    with pytest.raises(RuntimeError, match="no session contains expected minute"):
        session_quality(broken, et("2025-11-25", "10:00"), et("2025-11-25", "10:02"), [])


def test_coverage_pct_ignores_ambient_decimal_context():
    from decimal import Context, localcontext

    from core.dataquality import SessionQuality

    quality = SessionQuality(date(2025, 11, 25), 3, (et("2025-11-25", "10:00"),))
    assert quality.coverage_pct == D("66.67")
    with localcontext(Context(prec=3)):
        assert quality.coverage_pct == D("66.67")
