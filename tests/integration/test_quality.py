from collections import Counter
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    TICKER,
    FakeBarSource,
    FakeReference,
    count,
    feeds,
    flat_raw,
    scenario_bars,
    submit_default,
)
from tests.support import bar, et
from virtual_orders.evaluator.quality import run_end_of_day, run_session_quality, session_for_day
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, read_projection_row
from virtual_orders.ledger.runs import RunStatus, latest_run_status
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.sources import RawBar
from virtual_orders.storage.database import CYCLE_LOCK_KEY

SESSION = date(2025, 11, 25)


def end_of_day(engine, source, reference=None):
    return run_end_of_day(engine, gateway=feeds(source), reference=reference or FakeReference(),
                          session_day=SESSION, code_version=CODE_VERSION, market_now=et(DAY, "16:30"))


def events(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared for e in stored_events(conn, order_id)]


def without(bars: list[RawBar], start_hm: str, end_hm: str) -> list[RawBar]:
    return [b for b in bars if not et(DAY, start_hm) <= b.ts < et(DAY, end_hm)]


def test_full_coverage_emits_one_immutable_data_quality_event(engine):
    order_id = submit_default(engine).auto_order_id
    report = end_of_day(engine, FakeBarSource(scenario_bars()))
    (quality,) = [e for e in events(engine, order_id) if e.type == "DATA_QUALITY"]
    assert quality.event_key == "DATA_QUALITY:2025-11-25"
    assert quality.payload["expected_bars"] == 201 and quality.payload["missing_bars"] == 0
    assert quality.payload["coverage_pct"] == "100"
    assert quality.payload["evaluation_run_id"] == str(report.quality.run_id)
    assert quality.payload["data_as_of"] is not None
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
    assert (row["expected_bars"], row["missing_bars"]) == (201, 0)


def test_gap_missing_bar_reviews_and_d4_immutability(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(without(scenario_bars(), "10:10", "10:45"))
    touching = bar(et(DAY, "10:10"), 103, 103, 101.9, 103)  # touches entry_zone_high 102
    reference = FakeReference(minute={(TICKER, SESSION): {touching.ts: touching}})
    end_of_day(engine, source, reference)

    stored = events(engine, order_id)
    (quality,) = [e for e in stored if e.type == "DATA_QUALITY"]
    (gap,) = [e for e in stored if e.type == "DATA_GAP"]
    reviews = Counter(e.payload["reason"] for e in stored if e.type == "NEEDS_REVIEW")
    assert quality.payload["missing_bars"] == 35 and quality.payload["coverage_pct"] == "82.59"
    assert gap.event_key == f"DATA_GAP:{et(DAY, '10:10').isoformat()}" and gap.payload["minutes"] == 35
    assert reviews == {"MISSING_BAR_LEVEL_TOUCH": 1, "MISSING_BAR_UNVERIFIABLE": 34}

    source.load(flat_raw(DAY, "10:10", "10:45", 103))
    ingest_bars(engine, source, TICKER, et(DAY, "10:10"), et(DAY, "10:45"))
    again = run_session_quality(engine, session_day=SESSION, reference=reference, code_version=CODE_VERSION,
                                market_now=et(DAY, "16:30"))
    assert [o.event_keys for o in again.outcomes] == [()]
    assert [e.identity() for e in events(engine, order_id)] == [e.identity() for e in stored]


def test_daily_crosscheck_flags_full_session_window(engine):
    order_id = submit_default(engine).auto_order_id
    reference = FakeReference(daily={(TICKER, SESSION): (Decimal("110"), Decimal("105"))})
    end_of_day(engine, FakeBarSource(flat_raw(DAY, "09:30", "16:00", 105)), reference)
    reviews = [e.event_key for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    assert reviews == ["NEEDS_REVIEW:DAILY_RANGE_MISMATCH:2025-11-25"]


def test_matching_daily_range_is_not_flagged(engine):
    order_id = submit_default(engine).auto_order_id
    reference = FakeReference(daily={(TICKER, SESSION): (Decimal("105.2"), Decimal("104.8"))})
    end_of_day(engine, FakeBarSource(flat_raw(DAY, "09:30", "16:00", 105)), reference)
    assert [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"] == []


def test_partial_window_skips_crosscheck(engine):
    order_id = submit_default(engine).auto_order_id
    reference = FakeReference(daily={(TICKER, SESSION): (Decimal("200"), Decimal("50"))})
    end_of_day(engine, FakeBarSource(scenario_bars()), reference)
    assert [e for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"] == []


def test_reference_unavailable_marks_missing_minutes_unverifiable(engine):
    order_id = submit_default(engine).auto_order_id
    report = end_of_day(engine, FakeBarSource(without(scenario_bars(), "10:10", "10:12")), FakeReference(failing=True))
    reasons = [e.payload["reason"] for e in events(engine, order_id) if e.type == "NEEDS_REVIEW"]
    assert reasons == ["MISSING_BAR_UNVERIFIABLE", "MISSING_BAR_UNVERIFIABLE"]
    assert set(report.quality.unavailable) == {f"{TICKER}:1m", f"{TICKER}:1d"}


def test_end_of_day_expires_pending_order_without_last_bar(engine):
    order_id = submit_default(engine, valid_sessions=1).auto_order_id
    report = end_of_day(engine, FakeBarSource(flat_raw(DAY, "09:30", "15:59", 105)))
    assert [(o.order_id, o.event_keys) for o in report.expired] == [(order_id, ("EXPIRED",))]
    (quality,) = [e for e in events(engine, order_id) if e.type == "DATA_QUALITY"]
    assert (quality.payload["expected_bars"], quality.payload["missing_bars"]) == (390, 1)


def test_rebuild_accepts_quality_records_and_review_commands(engine):
    order_id = submit_default(engine).auto_order_id
    end_of_day(engine, FakeBarSource(without(scenario_bars(), "10:10", "10:45")), FakeReference())
    with engine.connect() as conn:
        projection_a = read_projection_row(conn, order_id)
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert rebuild_projection(engine, order_id) == projection_a


def test_non_session_day_is_rejected():
    with pytest.raises(ValueError, match="not a trading session"):
        session_for_day(date(2025, 11, 27))
    assert session_for_day(SESSION).close_utc == et(DAY, "16:00")


def test_projection_missing_order_is_quarantined_but_others_still_get_quality(engine):
    healthy_id = submit_default(engine).auto_order_id
    broken_id = submit_default(engine, client_signal_id="rex-2025-11-25-aapl-2").auto_order_id
    with engine.begin() as conn:
        delete_projection(conn, broken_id)

    report = end_of_day(engine, FakeBarSource(scenario_bars()))
    assert report.quality is not None
    outcomes_by_order = {o.order_id: o for o in report.quality.outcomes}
    assert outcomes_by_order[broken_id].error == errors.PROJECTION_MISSING
    assert outcomes_by_order[healthy_id].error is None

    (quality,) = [e for e in events(engine, healthy_id) if e.type == "DATA_QUALITY"]
    assert quality.event_key == "DATA_QUALITY:2025-11-25"
    assert count(engine, "integrity_incidents") >= 1

    with engine.connect() as conn:
        status, detail = latest_run_status(conn, report.quality.run_id)
    assert status is RunStatus.COMPLETED
    assert str(broken_id) in detail["integrity_errors"]


def test_end_of_day_skips_quality_when_cycle_is_skipped(engine):
    order_id = submit_default(engine).auto_order_id
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": CYCLE_LOCK_KEY})
        report = end_of_day(engine, FakeBarSource(scenario_bars()))
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CYCLE_LOCK_KEY})

    assert report.cycle.skipped
    assert report.expired == ()
    assert report.quality is None
    assert [e for e in events(engine, order_id) if e.type == "DATA_QUALITY"] == []
    assert count(engine, "evaluation_runs") == 0


def test_run_session_quality_rejects_an_open_session(engine):
    submit_default(engine)
    with pytest.raises(ValueError, match="just closed"):
        run_session_quality(engine, session_day=SESSION, reference=FakeReference(), code_version=CODE_VERSION,
                            market_now=et(DAY, "15:00"))


def test_run_session_quality_rejects_a_stale_session_day(engine):
    submit_default(engine)
    with pytest.raises(ValueError, match="just closed"):
        run_session_quality(engine, session_day=SESSION, reference=FakeReference(), code_version=CODE_VERSION,
                            market_now=et("2025-11-26", "16:30"))
