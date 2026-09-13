from decimal import Decimal

from sqlalchemy import select, text

from core.domain.models import Direction, Event, EventType
from core.fills import get_fill_model
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    TICKER,
    FakeBarSource,
    count,
    feeds,
    raw,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.cycle import evaluate_order, run_live_cycle
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import append_events, stored_events
from virtual_orders.ledger.orders import delete_projection, load_projection, lock_order, read_projection_row
from virtual_orders.ledger.runs import RunKind, RunStatus, latest_run_status, list_segments, start_run
from virtual_orders.ledger.writes import apply_result
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.storage import tables
from virtual_orders.storage.database import CYCLE_LOCK_KEY

V1 = get_fill_model("v1")


def cycle(engine, source, hm, **kwargs):
    return run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm), **kwargs)


def segments(engine, order_id):
    with engine.connect() as conn:
        return [(s.bar_from, s.bar_to, s.first_seq, s.event_count) for s in list_segments(conn, order_id)]


def test_three_cycles_fill_scale_out_and_close(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())

    first = cycle(engine, source, "10:30")
    assert first.outcomes[0].event_keys == ("FILLED",)
    assert first.outcomes[0].segment == (et(DAY, "09:30"), et(DAY, "10:29"))
    assert source.calls[0] == (TICKER, et(DAY, "09:30"), et(DAY, "10:30"))
    second = cycle(engine, source, "11:30")
    assert second.outcomes[0].event_keys == ("TARGET1_HIT",)
    assert source.calls[1][1] == et(DAY, "10:30")
    third = cycle(engine, source, "13:00")
    assert third.outcomes[0].event_keys == ("TARGET2_HIT",)
    assert cycle(engine, source, "13:30").outcomes == ()

    assert segments(engine, order_id) == [
        (et(DAY, "09:30"), et(DAY, "10:29"), 2, 1),
        (et(DAY, "10:30"), et(DAY, "11:29"), 3, 1),
        (et(DAY, "11:30"), et(DAY, "12:50"), 4, 1),
    ]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
        kinds = conn.execute(select(tables.evaluation_runs.c.kind)).scalars().all()
        status, detail = latest_run_status(conn, first.run_id)
    assert row["status"] == "CLOSED" and row["r_multiple"] == Decimal("1.75") and row["next_seq"] == 5
    assert set(kinds) == {"LIVE"} and len(kinds) == 4
    assert status is RunStatus.COMPLETED and detail["orders"] == 1


def test_trailing_missing_minute_is_not_consumed(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    late = next(b for b in scenario_bars() if b.ts == et(DAY, "10:29"))
    source.remove(TICKER, late.ts)
    cycle(engine, source, "10:30")
    source.load([late])
    cycle(engine, source, "10:45")
    assert [s[:2] for s in segments(engine, order_id)] == [
        (et(DAY, "09:30"), et(DAY, "10:28")), (et(DAY, "10:29"), et(DAY, "10:44")),
    ]


def test_internal_missing_minute_stays_missing_after_late_delivery(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    source.remove(TICKER, et(DAY, "10:05"))
    report = cycle(engine, source, "10:30")
    assert report.outcomes[0].event_keys == ()
    source.load([raw(DAY, "10:05", 101, 101.5, 100.5, 101.2)])
    cycle(engine, source, "10:45")
    with engine.connect() as conn:
        assert load_projection(conn, order_id).state.status.value == "PENDING"


def test_close_trailing_gap_consumes_missing_tail(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    source.remove(TICKER, et(DAY, "10:29"))
    cycle(engine, source, "10:30", close_trailing_gap=True)
    assert segments(engine, order_id)[0][:2] == (et(DAY, "09:30"), et(DAY, "10:29"))


def test_unclosed_minute_is_neither_read_nor_missing(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    report = run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, "10:30", 40),
                            close_trailing_gap=True)
    assert source.calls[0][2] == et(DAY, "10:30")
    assert report.outcomes[0].segment == (et(DAY, "09:30"), et(DAY, "10:29"))
    assert segments(engine, order_id)[0][:2] == (et(DAY, "09:30"), et(DAY, "10:29"))


def test_no_silent_provider_switch(engine):
    order_id = submit_default(engine).auto_order_id
    feed_key = f"{PRICE_SOURCE}:{TICKER}"
    backup = FakeBarSource(scenario_bars(), source="backup_feed")
    unknown = run_live_cycle(engine, feeds(backup), code_version=CODE_VERSION, market_now=et(DAY, "10:30"))
    assert unknown.ingest_failures[feed_key].startswith("UNKNOWN_DATA_SOURCE") and unknown.outcomes == ()

    primary = FakeBarSource(scenario_bars())
    primary.failing.add(TICKER)
    failed = run_live_cycle(engine, feeds(primary, backup), code_version=CODE_VERSION, market_now=et(DAY, "10:30"))
    assert feed_key in failed.ingest_failures and failed.outcomes == ()
    assert backup.calls == [] and segments(engine, order_id) == []


def test_cycle_skips_when_lock_is_held(engine):
    submit_default(engine)
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": CYCLE_LOCK_KEY})
        report = cycle(engine, FakeBarSource(scenario_bars()), "10:30")
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CYCLE_LOCK_KEY})
    assert report.skipped and count(engine, "evaluation_runs") == 0
    assert not cycle(engine, FakeBarSource(scenario_bars()), "10:30").skipped


def test_ingest_failure_skips_ticker_without_advancing_cursor(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    source.failing.add(TICKER)
    report = cycle(engine, source, "10:30")
    assert f"{PRICE_SOURCE}:{TICKER}" in report.ingest_failures and report.outcomes == ()
    assert segments(engine, order_id) == []
    with engine.connect() as conn:
        assert latest_run_status(conn, report.run_id)[1]["ingest_failures"] == report.ingest_failures


def test_frozen_orders_are_skipped_but_review_flags_keep_evaluating(engine):
    frozen_id = submit_default(engine, client_signal_id="frozen").auto_order_id
    flagged_id = submit_default(engine, client_signal_id="flagged").auto_order_id
    for order_id, command in ((frozen_id, "freeze"), (flagged_id, "flag_review")):
        with engine.begin() as conn:
            order = lock_order(conn, order_id)
            projection = load_projection(conn, order_id)
            result = getattr(V1, command)(projection.state, "SPLIT" if command == "freeze" else "MANUAL", "r")
            apply_result(conn, order, Direction.LONG, projection.next_seq, result)
    report = cycle(engine, FakeBarSource(scenario_bars()), "10:30")
    assert [(o.order_id, o.event_keys) for o in report.outcomes] == [(flagged_id, ("FILLED",))]


def test_integrity_error_isolates_one_order(engine):
    bad_id = submit_default(engine).auto_order_id
    good_id = submit_default(engine, client_signal_id="msft", ticker="MSFT").auto_order_id
    forged = Event(EventType.FILLED, "FILLED", et(DAY, "10:05"), price=Decimal("999"), qty=Decimal("1"))
    with engine.begin() as conn:
        order = lock_order(conn, bad_id)
        append_events(conn, order, [forged], load_projection(conn, bad_id).next_seq)
        conn.execute(text("UPDATE order_state SET next_seq = next_seq + 1 WHERE order_id = :id"), {"id": bad_id})
    source = FakeBarSource(scenario_bars() + scenario_bars(ticker="MSFT"))
    outcomes = {o.order_id: o for o in cycle(engine, source, "10:30").outcomes}
    assert outcomes[bad_id].error == errors.EVENT_HASH_CONFLICT
    assert outcomes[good_id].event_keys == ("FILLED",)
    with engine.connect() as conn:
        assert load_projection(conn, bad_id).state.frozen
        assert [e.prepared.event_key for e in stored_events(conn, bad_id)][-2:] == [
            "FROZEN:INTEGRITY", "NEEDS_REVIEW:INTEGRITY:EVENT_HASH_CONFLICT",
        ]
    assert count(engine, "integrity_incidents") == 1


def test_missing_projection_is_an_integrity_error(engine):
    order_id = submit_default(engine).auto_order_id
    with engine.begin() as conn:
        delete_projection(conn, order_id)
        run = start_run(conn, RunKind.LIVE, et(DAY, "23:00"), CODE_VERSION)
    outcome = evaluate_order(engine, order_id, run, market_now=et(DAY, "10:30"))
    assert outcome.error == errors.PROJECTION_MISSING
    assert count(engine, "integrity_incidents") == 1


def test_bars_are_read_as_of_the_run_watermark(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    report = cycle(engine, source, "10:30")
    assert report.data_as_of <= acquire_data_as_of(engine)
    with engine.connect() as conn:
        filled = [e for e in stored_events(conn, order_id) if e.prepared.type == "FILLED"][0]
        batch = conn.execute(select(tables.bar_batches.c.ingested_at).where(
            tables.bar_batches.c.batch_id == filled.prepared.bar_batch_id)).scalar_one()
    assert batch <= report.data_as_of
