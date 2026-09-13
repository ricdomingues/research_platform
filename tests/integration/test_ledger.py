import threading
import time
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from core.domain.calendar import evaluation_start_ts, signal_valid_until_ts
from core.domain.hashing import canonical_json
from core.domain.models import Direction, Event, EventType, FillConfig, OrderContext, OrderStatus, Origin
from core.fills import get_fill_model
from tests.integration.support import DAY, PRICE_SOURCE, count
from tests.support import et, long_signal
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import append_events, stored_events
from virtual_orders.ledger.orders import (
    OrderRow,
    SignalRow,
    delete_projection,
    find_signal_by_client_id,
    get_order,
    insert_signal,
    load_projection,
    lock_order,
    read_projection_row,
)
from virtual_orders.ledger.quarantine import run_guarded
from virtual_orders.ledger.runs import (
    RunKind,
    RunStatus,
    SegmentRow,
    finish_run,
    get_run,
    last_segment_end,
    latest_run_status,
    list_segments,
    record_segment,
    segment_containing,
    start_run,
)
from virtual_orders.ledger.writes import apply_result, persist_new_order
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.storage import tables

V1 = get_fill_model("v1")


def seed(engine, client_id="sig-1"):
    created_at = et(DAY, "09:00")
    spec = long_signal()
    calendar = calendar_for_window(created_at, created_at + timedelta(days=10))
    start = evaluation_start_ts(calendar, created_at)
    until = signal_valid_until_ts(calendar, start, spec.valid_sessions)
    signal = SignalRow(uuid4(), client_id, "hash", created_at, "test", "1", "unit", spec, start, until)
    config = FillConfig()
    order = OrderRow(uuid4(), signal.id, Origin.AUTO_STRATEGY, created_at, start, until, "v1", config,
                     "sha-test", config.risk_amount, PRICE_SOURCE)
    ctx = OrderContext(spec, config, calendar, start, until)
    created = V1.new_order_state(ctx)
    with engine.begin() as conn:
        assert insert_signal(conn, signal, {"client_signal_id": client_id})
        persist_new_order(conn, order, Direction.LONG, created)
    return signal, order, ctx, created


def test_new_order_persists_created_event_and_projection(engine):
    signal, order, ctx, created = seed(engine)
    (event,) = created.events
    with engine.connect() as conn:
        (stored,) = stored_events(conn, order.id)
        projection = load_projection(conn, order.id)
        row = read_projection_row(conn, order.id)
        assert get_order(conn, order.id) == order
        assert find_signal_by_client_id(conn, "sig-1") == signal
    assert stored.seq == 1 and stored.prepared.payload_hash == event.payload_hash
    assert stored.prepared.hash_material == canonical_json(event.hash_material())
    assert projection.state == created.state and projection.next_seq == 2
    assert row["status"] == "PENDING" and row["r_multiple"] == 0 and row["expected_bars"] == 0
    assert "updated_at" not in row


def test_duplicate_signal_insert_returns_false(engine):
    signal, *_ = seed(engine)
    with engine.begin() as conn:
        assert not insert_signal(conn, replace(signal, id=uuid4()), {})


def test_same_key_same_hash_is_noop(engine):
    _, order, _, created = seed(engine)
    with engine.begin() as conn:
        result = append_events(conn, order, created.events, next_seq=2)
    assert result.inserted == () and result.next_seq == 2
    assert count(engine, "order_events") == 1


def test_same_key_different_hash_aborts_records_incident_and_freezes(engine):
    _, order, _, _ = seed(engine)
    innocent = Event(EventType.NEEDS_REVIEW, "NEEDS_REVIEW:X:1", payload={"reason": "X", "ref": "1"})
    tampered = Event(EventType.ORDER_CREATED, "ORDER_CREATED", payload={"tampered": True})

    def attempt():
        with engine.begin() as conn:
            append_events(conn, order, [innocent, tampered], next_seq=2)

    with pytest.raises(errors.LedgerIntegrityError) as caught:
        run_guarded(engine, attempt)
    assert caught.value.kind == errors.EVENT_HASH_CONFLICT
    with engine.connect() as conn:
        keys = [e.prepared.event_key for e in stored_events(conn, order.id)]
        incident = conn.execute(select(tables.integrity_incidents)).one()
        projection = load_projection(conn, order.id)
    assert keys == ["ORDER_CREATED", "FROZEN:INTEGRITY", "NEEDS_REVIEW:INTEGRITY:EVENT_HASH_CONFLICT"]
    assert incident.kind == errors.EVENT_HASH_CONFLICT and incident.event_key == "ORDER_CREATED"
    assert incident.existing_hash != incident.attempted_hash
    assert projection.state.frozen and projection.state.review_reasons == ("INTEGRITY",)
    assert projection.next_seq == 4

    with pytest.raises(errors.LedgerIntegrityError):
        run_guarded(engine, attempt)
    assert count(engine, "integrity_incidents") == 2 and count(engine, "order_events") == 3


def test_market_event_before_evaluation_start_is_rejected(engine):
    _, order, ctx, _ = seed(engine)
    early = Event(EventType.TRIGGER_HIT, "TRIGGER_HIT", ctx.evaluation_start_ts - timedelta(minutes=1))
    with pytest.raises(errors.LedgerIntegrityError) as caught:
        with engine.begin() as conn:
            append_events(conn, order, [early], next_seq=2)
    assert caught.value.kind == errors.EVENT_BEFORE_EVALUATION_START


def test_tampered_stored_hash_is_detected_on_read(engine):
    _, order, _, _ = seed(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE order_events DISABLE TRIGGER USER"))
        conn.execute(text("UPDATE order_events SET hash_material = hash_material || ' '"))
        conn.execute(text("ALTER TABLE order_events ENABLE TRIGGER USER"))
    with engine.connect() as conn, pytest.raises(errors.LedgerIntegrityError) as caught:
        stored_events(conn, order.id)
    assert caught.value.kind == errors.STORED_HASH_MISMATCH


def test_apply_result_appends_and_updates_projection(engine):
    _, order, _, created = seed(engine)
    flagged = V1.flag_review(created.state, "MANUAL_CHECK", "r1")
    with engine.begin() as conn:
        result = apply_result(conn, order, Direction.LONG, 2, flagged)
        row = read_projection_row(conn, order.id)
    assert result.first_seq == 2 and result.next_seq == 3
    assert row["needs_review"] is True and row["next_seq"] == 3
    with engine.begin() as conn:
        delete_projection(conn, order.id)
        assert load_projection(conn, order.id) is None


def test_runs_are_append_only_with_latest_status(engine):
    with engine.begin() as conn:
        run = start_run(conn, RunKind.LIVE, et(DAY, "12:00"), "sha", detail={"n": 1})
        finish_run(conn, run.run_id, RunStatus.COMPLETED, {"orders": 3})
    with engine.connect() as conn:
        assert get_run(conn, run.run_id) == run
        assert latest_run_status(conn, run.run_id) == (RunStatus.COMPLETED, {"orders": 3})
    assert count(engine, "evaluation_run_status") == 2


def test_segments_order_by_seq_then_time(engine):
    _, order, _, _ = seed(engine)
    with engine.begin() as conn:
        runs = [start_run(conn, RunKind.LIVE, et(DAY, "16:00"), "sha") for _ in range(3)]
        record_segment(conn, SegmentRow(order.id, runs[0].run_id, et(DAY, "10:31"), et(DAY, "11:00"), "h2", 2, 0))
        record_segment(conn, SegmentRow(order.id, runs[1].run_id, et(DAY, "09:30"), et(DAY, "10:30"), "h1", 2, 0))
        record_segment(conn, SegmentRow(order.id, runs[2].run_id, et(DAY, "11:01"), et(DAY, "11:30"), "h3", 2, 1))
    with engine.connect() as conn:
        segments = list_segments(conn, order.id)
        assert [s.selected_data_hash for s in segments] == ["h1", "h2", "h3"]
        assert last_segment_end(conn, order.id) == et(DAY, "11:30")
        assert segment_containing(conn, order.id, et(DAY, "10:45")).selected_data_hash == "h2"
        assert segment_containing(conn, order.id, et(DAY, "12:00")) is None


def test_lock_order_blocks_concurrent_writer(engine):
    _, order, _, _ = seed(engine)
    holder = engine.connect()
    transaction = holder.begin()
    lock_order(holder, order.id)
    done = threading.Event()

    def contender():
        with engine.begin() as conn:
            lock_order(conn, order.id)
            done.set()

    worker = threading.Thread(target=contender)
    worker.start()
    time.sleep(0.3)
    assert not done.is_set()
    transaction.commit()
    holder.close()
    worker.join(timeout=5)
    assert done.is_set()


def test_unknown_order_raises_not_found(engine):
    with engine.connect() as conn, pytest.raises(errors.OrderNotFound):
        get_order(conn, uuid4())


def test_projection_status_enum_round_trip(engine):
    _, order, _, _ = seed(engine)
    with engine.connect() as conn:
        assert load_projection(conn, order.id).state.status is OrderStatus.PENDING
