from collections import Counter
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    TICKER,
    FakeBarSource,
    FakeReference,
    count,
    feeds,
    flat_raw,
    raw,
    submit_default,
)
from tests.support import bar, et
from virtual_orders.evaluator import recheck as recheck_module
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.evaluator.recheck import (
    RecheckReport,
    last_closed_session_day,
    levels_state_at_close,
    run_quality_recheck,
    sessions_closed_after,
)
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, load_projection, read_projection_row
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, latest_run_status, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.orders import order_detail
from virtual_orders.readmodels.quality import PendingQuality, pending_quality_sessions
from virtual_orders.storage import tables

NEXT = "2025-11-26"
SESSION, NEXT_SESSION = date(2025, 11, 25), date(2025, 11, 26)


def session_bars(day, missing=None):
    bars = flat_raw(day, "09:30", "16:00", 105)  # above the zone: the order never fills
    if missing is not None:
        start, end = missing
        bars = [b for b in bars if not et(day, start) <= b.ts < et(day, end)]
    return bars


def end_of_day(engine, source, session_day, market_now):
    return run_end_of_day(engine, gateway=feeds(source), reference=FakeReference(), session_day=session_day,
                          code_version=CODE_VERSION, market_now=market_now)


def recheck(engine, source, market_now, reference=None):
    return run_quality_recheck(engine, gateway=feeds(source), reference=reference or FakeReference(),
                               code_version=CODE_VERSION, market_now=market_now)


def events(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared for e in stored_events(conn, order_id)]


def skipped_first_session(engine, bars):
    """Session DAY is skipped by D12 (provider failure); session NEXT closes normally with data restored."""
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(bars)
    source.failing.add(TICKER)
    first = end_of_day(engine, source, SESSION, et(DAY, "16:30"))
    with engine.connect() as conn:
        _, detail = latest_run_status(conn, first.quality.run_id)
    assert detail["not_evaluated"] == {str(order_id): "PROVIDER_FAILURE"}
    source.failing.clear()
    end_of_day(engine, source, NEXT_SESSION, et(NEXT, "16:30"))
    return order_id, source


def test_last_closed_session_day_follows_the_calendar():
    assert last_closed_session_day(et(DAY, "15:59")) == date(2025, 11, 24)
    assert last_closed_session_day(et(DAY, "16:00")) == SESSION
    assert last_closed_session_day(et("2025-11-27", "12:00")) == NEXT_SESSION  # holiday


def test_recheck_consumes_a_session_skipped_by_d12_exactly_once(engine):
    order_id, source = skipped_first_session(engine, session_bars(DAY) + session_bars(NEXT))
    key = f"{order_id}:{DAY}"

    report = recheck(engine, source, et(NEXT, "16:45"))

    assert report.not_evaluated == {} and set(report.rechecked) == {key}
    assert report.rechecked[key].startswith(f"DATA_QUALITY_RECHECK:{DAY}:")
    with engine.connect() as conn:
        row = conn.execute(select(tables.data_quality_rechecks)).one()
        status, detail = latest_run_status(conn, report.run_id)
        assert pending_quality_sessions(conn) == []
    assert status is RunStatus.COMPLETED and detail["rechecked"] == report.rechecked
    assert row.order_id == order_id and row.session_date == SESSION and row.run_id == report.run_id
    assert row.recheck_key == report.rechecked[key]
    assert row.payload["status"] == "EVALUATED" and row.payload["not_evaluated_reason"] == "PROVIDER_FAILURE"
    assert (row.payload["expected_bars"], row.payload["missing_bars"]) == (390, 0)
    # D4: the skipped session never receives a DATA_QUALITY event; only NEXT has one.
    assert [e.event_key for e in events(engine, order_id) if e.type == "DATA_QUALITY"] == [f"DATA_QUALITY:{NEXT}"]

    again = recheck(engine, source, et(NEXT, "17:00"))
    assert again == RecheckReport(None, {}, {}) and count(engine, "data_quality_rechecks") == 1
    with engine.connect() as conn:
        rechecks = order_detail(conn, order_id)["data_quality"]["rechecks"]
    assert [r["recheck_key"] for r in rechecks] == [report.rechecked[key]]


def test_recheck_flags_reviews_but_never_writes_data_quality_or_data_gap(engine):
    order_id, source = skipped_first_session(engine, session_bars(DAY, ("10:10", "10:45")) + session_bars(NEXT))

    report = recheck(engine, source, et(NEXT, "16:45"))

    (row,) = [r for r in _rows(engine)]
    assert row.payload["missing_bars"] == 35
    (gap,) = row.payload["gaps"]
    assert datetime.fromisoformat(gap["gap_start_ts"]) == et(DAY, "10:10") and gap["minutes"] == 35
    stored = events(engine, order_id)
    assert Counter(e.payload["reason"] for e in stored if e.type == "NEEDS_REVIEW") == {"MISSING_BAR_UNVERIFIABLE": 35}
    assert [e for e in stored if e.type == "DATA_GAP"] == []
    assert len(report.outcomes) == 1 and report.outcomes[0].error is None
    with engine.connect() as conn:
        projection = read_projection_row(conn, order_id)
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert rebuild_projection(engine, order_id) == projection


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.data_quality_rechecks)).all()


def test_the_just_closed_session_is_left_to_end_of_day(engine):
    submit_default(engine)
    source = FakeBarSource(session_bars(DAY))
    source.failing.add(TICKER)
    end_of_day(engine, source, SESSION, et(DAY, "16:30"))
    source.failing.clear()

    assert recheck(engine, source, et(DAY, "17:00")) == RecheckReport(None, {}, {})
    with engine.connect() as conn:
        runs = conn.execute(select(func.count()).select_from(tables.evaluation_runs)
                            .where(tables.evaluation_runs.c.kind == "QUALITY_RECHECK")).scalar_one()
    assert runs == 0


def never_observed_first_session(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(session_bars(NEXT))  # nothing at all for DAY
    end_of_day(engine, source, SESSION, et(DAY, "16:30"))
    end_of_day(engine, source, NEXT_SESSION, et(NEXT, "16:30"))
    return order_id, source


def test_sessions_closed_after_counts_calendar_sessions():
    assert sessions_closed_after(SESSION, et(NEXT, "16:45")) == 1
    assert sessions_closed_after(SESSION, et("2025-12-03", "15:59")) == 4  # 11-26, 11-28 (half day), 12-01, 12-02
    assert sessions_closed_after(SESSION, et("2025-12-03", "16:45")) == 5


def test_no_observations_inside_the_lookback_keeps_the_session_pending(engine):
    order_id, source = never_observed_first_session(engine)

    report = recheck(engine, source, et(NEXT, "16:45"))

    assert report.rechecked == {} and report.terminal == {}
    assert report.not_evaluated == {f"{order_id}:{DAY}": "NO_OBSERVATIONS"}
    assert count(engine, "data_quality_rechecks") == 0
    with engine.connect() as conn:
        assert [p.order_id for p in pending_quality_sessions(conn)] == [order_id]


def test_no_observations_after_the_lookback_gets_a_final_row_and_clears_the_pending(engine):
    order_id, source = never_observed_first_session(engine)
    key = f"{order_id}:{DAY}"

    report = recheck(engine, source, et("2025-12-03", "16:45"))  # five sessions closed after DAY

    assert report.terminal == {key: "NO_OBSERVATIONS"} and set(report.rechecked) == {key}
    assert report.not_evaluated == {}
    (row,) = _rows(engine)
    assert row.payload["status"] == "NO_OBSERVATIONS_FINAL" and row.payload["terminal_reason"] == "NO_OBSERVATIONS"
    assert not [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    with engine.connect() as conn:
        assert pending_quality_sessions(conn) == []
        _, detail = latest_run_status(conn, report.run_id)
    assert detail["terminal"] == {key: "NO_OBSERVATIONS"}


def test_a_session_outside_the_order_window_gets_a_terminal_row(engine):
    order_id = submit_default(engine).auto_order_id  # evaluation starts on DAY
    before = date(2025, 11, 24)
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        eod = start_run(conn, RunKind.END_OF_DAY, as_of, CODE_VERSION, detail={"session_day": before})
        finish_run(conn, eod.run_id, RunStatus.COMPLETED,
                   {"session_day": before, "not_evaluated": {str(order_id): "NO_OBSERVATIONS"}})
    key = f"{order_id}:2025-11-24"

    report = recheck(engine, FakeBarSource(), et(DAY, "16:45"))

    assert report.terminal == {key: "OUTSIDE_WINDOW"} and report.not_evaluated == {}
    (row,) = _rows(engine)
    assert row.payload["status"] == "NOT_MEASURABLE" and row.payload["terminal_reason"] == "OUTSIDE_WINDOW"
    assert not [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    with engine.connect() as conn:
        assert pending_quality_sessions(conn) == []


def test_a_second_recheck_row_for_the_same_session_is_never_written(engine, monkeypatch):
    order_id, source = skipped_first_session(engine, session_bars(DAY) + session_bars(NEXT))
    with engine.connect() as conn:
        stale = pending_quality_sessions(conn)
    recheck(engine, source, et(NEXT, "16:45"))
    monkeypatch.setattr(recheck_module, "pending_quality_sessions", lambda conn: stale)  # an overlapping run's view

    report = recheck(engine, source, et(NEXT, "17:00"))

    assert report.not_evaluated == {f"{order_id}:{DAY}": "ALREADY_RECHECKED"} and report.rechecked == {}
    assert count(engine, "data_quality_rechecks") == 1


def moved_stop_bars():
    """DAY: fill at 10:05 and a feed gap 10:10-10:45; NEXT: TARGET1 at 11:00 moves the stop to the entry."""
    first = flat_raw(DAY, "09:30", "10:05", 105) + [raw(DAY, "10:05", 101, 101.5, 100.5, 101.2)] + flat_raw(
        DAY, "10:06", "16:00", 103
    )
    following = flat_raw(NEXT, "09:30", "11:00", 103) + [raw(NEXT, "11:00", 105, 106, 104.8, 105.5)] + flat_raw(
        NEXT, "11:01", "16:00", 105
    )
    return [b for b in first if not et(DAY, "10:10") <= b.ts < et(DAY, "10:45")] + following


def touch_at_the_original_stop():
    minute = et(DAY, "10:20")
    return FakeReference(minute={(TICKER, SESSION): {minute: bar(minute, 98, 98, 96.5, 97.5)}})


def review_reasons(engine, order_id):
    return Counter(e.payload["reason"] for e in events(engine, order_id) if e.type == "NEEDS_REVIEW")


def test_level_touch_uses_the_stop_in_force_during_the_rechecked_session(engine):
    order_id, source = skipped_first_session(engine, moved_stop_bars())
    with engine.connect() as conn:
        state = load_projection(conn, order_id).state
        prepared = [e.prepared for e in stored_events(conn, order_id)]
    assert state.stop_current != Decimal("97") and state.stop_active_from is not None  # moved on NEXT

    at_day = levels_state_at_close(state, Decimal("97"), prepared, "v1", et(DAY, "16:00"))
    at_next = levels_state_at_close(state, Decimal("97"), prepared, "v1", et(NEXT, "16:00"))
    assert at_day is not None and (at_day.stop_current, at_day.stop_active_from) == (Decimal("97"), None)
    assert at_next is not None and (at_next.stop_previous, at_next.stop_current) == (Decimal("97"), state.stop_current)
    assert levels_state_at_close(state, Decimal("97"), prepared, "v2", et(DAY, "16:00")) is None

    report = recheck(engine, source, et(NEXT, "16:45"), reference=touch_at_the_original_stop())

    (row,) = _rows(engine)
    assert row.payload["level_touch_check"] == "LEDGER_AT_SESSION_CLOSE"
    assert row.payload["level_touch_unchecked_minutes"] == []
    assert review_reasons(engine, order_id) == {"MISSING_BAR_UNVERIFIABLE": 34, "MISSING_BAR_LEVEL_TOUCH": 1}
    assert report.outcomes[0].error is None


def test_level_touch_is_skipped_with_a_reason_when_levels_cannot_be_derived(engine, monkeypatch):
    order_id, source = skipped_first_session(engine, moved_stop_bars())
    monkeypatch.setattr(recheck_module, "LEVEL_DERIVATION_MODELS", frozenset())

    recheck(engine, source, et(NEXT, "16:45"), reference=touch_at_the_original_stop())

    (row,) = _rows(engine)
    assert row.payload["level_touch_check"] == "SKIPPED:LEVELS_NOT_DERIVABLE"
    assert [datetime.fromisoformat(m) for m in row.payload["level_touch_unchecked_minutes"]] == [et(DAY, "10:20")]
    assert review_reasons(engine, order_id) == {"MISSING_BAR_UNVERIFIABLE": 34}  # never the current levels


def test_provider_failure_during_the_recheck_follows_d12(engine):
    order_id, source = skipped_first_session(engine, session_bars(DAY) + session_bars(NEXT))
    source.failing.add(TICKER)

    report = recheck(engine, source, et(NEXT, "16:45"))

    assert report.not_evaluated == {f"{order_id}:{DAY}": "PROVIDER_FAILURE"} and report.rechecked == {}
    with engine.connect() as conn:
        _, detail = latest_run_status(conn, report.run_id)
    assert detail["ingest_failures"] == {f"fake_feed:{TICKER}:{DAY}": "SourceUnavailable"}


def test_naive_market_now_is_rejected(engine):
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        run_quality_recheck(engine, gateway=feeds(FakeBarSource()), reference=FakeReference(),
                            code_version=CODE_VERSION, market_now=datetime(2025, 11, 26, 21, 0))  # noqa: DTZ001


def test_provider_failure_after_the_lookback_gets_a_final_row_a_review_and_clears_the_pending(engine):
    order_id, source = skipped_first_session(engine, session_bars(DAY) + session_bars(NEXT))
    source.failing.add(TICKER)  # the feed is still down five sessions later
    key = f"{order_id}:{DAY}"

    report = recheck(engine, source, et("2025-12-03", "16:45"))  # five sessions closed after DAY

    assert report.terminal == {key: "PROVIDER_FAILURE"} and set(report.rechecked) == {key}
    assert report.not_evaluated == {}
    (row,) = _rows(engine)
    assert row.payload["status"] == "PROVIDER_FAILURE_FINAL" and row.payload["terminal_reason"] == "PROVIDER_FAILURE"
    reviews = [(e.event_key, e.payload["reason"], e.payload["ref"])
               for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    assert reviews == [(f"NEEDS_REVIEW:DATA_QUALITY_UNVERIFIED:{DAY}", "DATA_QUALITY_UNVERIFIED", DAY)]  # spec 6
    again = flag_order_review(engine, order_id, reason="DATA_QUALITY_UNVERIFIED", ref=DAY)
    assert again.error is None and again.event_keys == ()  # idempotent by event_key
    with engine.connect() as conn:
        assert pending_quality_sessions(conn) == []
        assert order_detail(conn, order_id)["order"]["needs_review"] is True  # leaves the default metrics (D34)


def test_a_session_evaluated_by_end_of_day_after_the_pending_read_is_left_alone(engine, monkeypatch):
    """T10: the pending list is read outside the order lock, so END_OF_DAY can write DATA_QUALITY in between."""
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(session_bars(DAY) + session_bars(NEXT))
    first = end_of_day(engine, source, SESSION, et(DAY, "16:30"))
    end_of_day(engine, source, NEXT_SESSION, et(NEXT, "16:30"))
    assert first.quality is not None
    assert f"DATA_QUALITY:{DAY}" in [e.event_key for e in events(engine, order_id)]
    stale = PendingQuality(order_id, SESSION, first.quality.run_id, "PROVIDER_FAILURE", TICKER, PRICE_SOURCE)
    monkeypatch.setattr(recheck_module, "pending_quality_sessions", lambda conn: [stale])  # read before END_OF_DAY

    report = recheck(engine, source, et(NEXT, "16:45"))

    assert report.not_evaluated == {f"{order_id}:{DAY}": "ALREADY_EVALUATED"}
    assert report.rechecked == {} and report.terminal == {}
    assert count(engine, "data_quality_rechecks") == 0
    assert not [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
