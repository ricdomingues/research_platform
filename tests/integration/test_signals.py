import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.domain.calendar import SessionCalendar
from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    SIGNAL_CREATED_AT,
    count,
    signal_body,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator import context as context_module
from virtual_orders.evaluator.context import load_order_context
from virtual_orders.evaluator.signals import IdempotencyConflict, SignalValidationError, submit_signal
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import get_order, get_signal, read_projection_row
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.storage import tables


def test_signal_created_with_auto_order(engine):
    result = submit_default(engine)
    assert result.status == "CREATED" and result.auto_order_id is not None
    with engine.connect() as conn:
        signal = get_signal(conn, result.signal_id)
        order = get_order(conn, result.auto_order_id)
        (created,) = stored_events(conn, order.id)
        row = read_projection_row(conn, order.id)
        raw_payload = conn.execute(select(tables.signals.c.raw_payload)).scalar_one()
    assert signal.evaluation_start_ts == et(DAY, "09:30")
    assert signal.valid_until_ts == et("2025-11-28", "13:00")  # 11-25, 11-26, half day 11-28
    assert order.created_at == SIGNAL_CREATED_AT and order.valid_until_ts == signal.valid_until_ts
    assert order.fill_model_version == "v1" and order.code_version == CODE_VERSION
    assert order.price_source == PRICE_SOURCE
    assert created.prepared.event_key == "ORDER_CREATED" and row["status"] == "PENDING"
    assert created.prepared.payload["signal"]["stop"] == "97"
    assert raw_payload["entry_zone_low"] == "100"


def test_same_body_returns_existing_resource(engine):
    first = submit_default(engine)
    again = submit_signal(engine, dict(reversed(list(signal_body(entry_zone_low=Decimal("100.0")).items()))),
                          config=FillConfig(), code_version="other", price_source="other_feed", now=et(DAY, "11:00"))
    assert (again.status, again.signal_id, again.auto_order_id) == ("EXISTING", first.signal_id, first.auto_order_id)
    assert count(engine, "signals") == 1 and count(engine, "orders") == 1


def test_same_client_id_with_different_body_conflicts(engine):
    first = submit_default(engine)
    with pytest.raises(IdempotencyConflict) as caught:
        submit_default(engine, stop=Decimal("96"))
    assert caught.value.existing_signal_id == first.signal_id
    assert count(engine, "signals") == 1


def test_auto_order_can_be_disabled(engine):
    result = submit_default(engine, auto_order=False)
    assert result.auto_order_id is None and count(engine, "orders") == 0


@pytest.mark.parametrize("overrides, expected", [
    ({"stop": Decimal("101")}, ["STOP_NOT_BEYOND_ZONE"]),
    ({"ticker": None}, ["MISSING_FIELD:ticker"]),
    ({"direction": "SIDEWAYS"}, ["INVALID_DIRECTION"]),
    ({"valid_sessions": 21}, ["VALID_SESSIONS_OUT_OF_RANGE"]),
    ({"valid_sessions": "3"}, ["INVALID_INTEGER:valid_sessions"]),
    ({"target2": "abc"}, ["INVALID_DECIMAL:target2"]),
    ({"auto_order": "yes"}, ["INVALID_BOOLEAN:auto_order"]),
])
def test_invalid_signals_are_rejected_without_writes(engine, overrides, expected):
    with pytest.raises(SignalValidationError) as caught:
        submit_default(engine, **overrides)
    assert caught.value.errors == expected
    assert count(engine, "signals") == 0


def test_float_body_is_not_canonical(engine):
    with pytest.raises(SignalValidationError) as caught:
        submit_default(engine, stop=97.0)
    assert caught.value.errors[0].startswith("NON_CANONICAL_BODY")


def test_short_signal_is_accepted(engine):
    result = submit_default(engine, client_signal_id="short-1", direction="SHORT", entry_zone_low=Decimal("100"),
                            entry_zone_high=Decimal("102"), stop=Decimal("105"), target1=Decimal("95"),
                            target2=Decimal("90"))
    assert result.status == "CREATED"


def test_concurrent_duplicate_submissions_create_one_signal(engine):
    barrier = threading.Barrier(4)
    results = []

    def submit():
        barrier.wait()
        results.append(submit_default(engine))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(r.status for r in results) == ["CREATED", "EXISTING", "EXISTING", "EXISTING"]
    assert count(engine, "signals") == 1 and count(engine, "orders") == 1


def test_calendar_identity_is_verified_when_loading_context(engine, monkeypatch):
    result = submit_default(engine)
    with engine.connect() as conn:
        order = get_order(conn, result.auto_order_id)
        _, ctx = load_order_context(conn, order)
    assert ctx.evaluation_start_ts == et(DAY, "09:30")

    real = calendar_for_window(et(DAY, "09:00"), et("2025-11-28", "13:00"))
    without_wednesday = SessionCalendar([s for s in real.sessions if s.day != date(2025, 11, 26)])
    monkeypatch.setattr(context_module, "calendar_for_window", lambda earliest, latest: without_wednesday)
    with engine.connect() as conn, pytest.raises(errors.LedgerIntegrityError) as caught:
        load_order_context(conn, order)
    assert caught.value.kind == errors.CALENDAR_MISMATCH
