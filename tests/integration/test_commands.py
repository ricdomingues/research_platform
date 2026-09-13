import threading
import time
from decimal import Decimal

import pytest

from core.domain.models import Direction
from core.fills import get_fill_model
from tests.integration.support import CODE_VERSION, DAY, FakeBarSource, feeds, flat_raw, scenario_bars, submit_default
from tests.support import et
from virtual_orders.evaluator.commands import (
    OrderAlreadyFinal,
    cancel_order,
    expire_due_orders,
    finalize_validity,
    flag_order_review,
    freeze_order,
)
from virtual_orders.evaluator.cycle import evaluate_order, run_live_cycle
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import load_projection, lock_order, read_projection_row
from virtual_orders.ledger.runs import RunKind, start_run
from virtual_orders.ledger.writes import apply_result
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.marketdata.ingest import ingest_bars

V1 = get_fill_model("v1")
AFTER_VALIDITY = et("2025-11-28", "16:30")


def keys(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.event_key for e in stored_events(conn, order_id)]


def cycle(engine, source, hm):
    return run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))


def test_cancel_pending_order_is_final_and_not_evaluated(engine):
    order_id = submit_default(engine).auto_order_id
    outcome = cancel_order(engine, order_id, at=et(DAY, "09:45"), requested_by="ricardo")
    assert outcome.event_keys == ("CANCELED",)
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
    assert row["status"] == "CANCELED" and row["final_event_ts"] == et(DAY, "09:45")
    with pytest.raises(OrderAlreadyFinal):
        cancel_order(engine, order_id, at=et(DAY, "09:46"))
    assert cycle(engine, FakeBarSource(scenario_bars()), "10:30").outcomes == ()


def test_cancel_with_open_position_records_open_qty(engine):
    order_id = submit_default(engine).auto_order_id
    cycle(engine, FakeBarSource(scenario_bars()), "10:30")
    cancel_order(engine, order_id, at=et(DAY, "10:31"))
    with engine.connect() as conn:
        canceled = stored_events(conn, order_id)[-1].prepared
    assert canceled.payload["open_qty"] == "25"


def test_cancel_waits_for_worker_lock(engine):
    order_id = submit_default(engine).auto_order_id
    holder = engine.connect()
    transaction = holder.begin()
    lock_order(holder, order_id)
    results = []
    worker = threading.Thread(target=lambda: results.append(cancel_order(engine, order_id, at=et(DAY, "10:00"))))
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive()
    transaction.commit()
    holder.close()
    worker.join(timeout=5)
    assert results[0].event_keys == ("CANCELED",)


def test_worker_reloads_projection_after_waiting_for_cancel(engine):
    order_id = submit_default(engine).auto_order_id
    ingest_bars(engine, FakeBarSource(scenario_bars()), "AAPL", et(DAY, "09:30"), et(DAY, "10:30"))
    with engine.begin() as conn:
        run = start_run(conn, RunKind.LIVE, acquire_data_as_of(engine), CODE_VERSION)
    holder = engine.connect()
    transaction = holder.begin()
    order = lock_order(holder, order_id)
    projection = load_projection(holder, order_id)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(evaluate_order(engine, order_id, run, market_now=et(DAY, "10:30")))
    )
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive()
    apply_result(holder, order, Direction.LONG, projection.next_seq,
                 V1.cancel(projection.state, et(DAY, "10:00"), "user"))
    transaction.commit()
    holder.close()
    worker.join(timeout=5)
    assert results[0].event_keys == () and results[0].error is None
    assert keys(engine, order_id) == ["ORDER_CREATED", "CANCELED"]


def test_pending_order_expires_after_validity(engine):
    order_id = submit_default(engine).auto_order_id
    assert finalize_validity(engine, order_id, now=et("2025-11-28", "12:59")).event_keys == ()
    outcome = finalize_validity(engine, order_id, now=AFTER_VALIDITY)
    assert outcome.event_keys == ("EXPIRED",)
    with engine.connect() as conn:
        assert read_projection_row(conn, order_id)["final_event_ts"] == et("2025-11-28", "13:00")


def test_open_position_time_exit_uses_processed_bar_version(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    cycle(engine, source, "10:30")
    source.load(flat_raw(DAY, "10:29", "10:30", 250))  # vendor correction after processing
    ingest_bars(engine, source, "AAPL", et(DAY, "10:29"), et(DAY, "10:30"))
    outcome = finalize_validity(engine, order_id, now=AFTER_VALIDITY)
    assert outcome.event_keys == ("TIME_EXIT", f"NEEDS_REVIEW:STALE_EXIT_BAR:{et('2025-11-28', '13:00').isoformat()}")
    with engine.connect() as conn:
        exit_event = [e.prepared for e in stored_events(conn, order_id) if e.prepared.type == "TIME_EXIT"][0]
    assert exit_event.bar_ts == et(DAY, "10:29")
    assert exit_event.payload["raw_price"] == "103"
    assert exit_event.price == Decimal("102.9485")


def test_expire_due_orders_only_touches_due_orders(engine):
    due = submit_default(engine).auto_order_id
    later = submit_default(engine, client_signal_id="later", valid_sessions=10).auto_order_id
    outcomes = expire_due_orders(engine, now=AFTER_VALIDITY)
    assert [(o.order_id, o.event_keys) for o in outcomes] == [(due, ("EXPIRED",))]
    assert keys(engine, later) == ["ORDER_CREATED"]


def test_freeze_and_review_commands_are_idempotent(engine):
    order_id = submit_default(engine).auto_order_id
    assert freeze_order(engine, order_id, reason="SPLIT", ref="2025-11-26").event_keys == (
        "FROZEN:SPLIT", "NEEDS_REVIEW:SPLIT:2025-11-26",
    )
    assert freeze_order(engine, order_id, reason="SPLIT", ref="2025-11-26").event_keys == ()
    assert flag_order_review(engine, order_id, reason="MANUAL", ref="x").event_keys == ("NEEDS_REVIEW:MANUAL:x",)
    assert flag_order_review(engine, order_id, reason="MANUAL", ref="x").event_keys == ()
    assert expire_due_orders(engine, now=AFTER_VALIDITY) == []
