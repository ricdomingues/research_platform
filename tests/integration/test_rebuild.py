from decimal import Decimal

import pytest
from sqlalchemy import select, text

from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    TICKER,
    FakeBarSource,
    backdated_batch,
    count,
    feeds,
    flat_raw,
    raw,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.commands import cancel_order, finalize_validity, flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.evaluator.rebuild import rebuild_all_projections, rebuild_projection
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, load_projection, read_projection_row
from virtual_orders.ledger.runs import get_run, list_segments
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.storage import tables

AFTER_VALIDITY = et("2025-11-28", "16:30")


def cycles(engine, source, *hms):
    for hm in hms:
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))


def snapshot(engine, order_id):
    with engine.connect() as conn:
        return read_projection_row(conn, order_id)


def delete_and_rebuild(engine, order_id):
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert snapshot(engine, order_id) is None
    rebuilt = rebuild_projection(engine, order_id)
    return rebuilt, snapshot(engine, order_id)


def test_d2_projection_rebuilt_from_history_equals_original(engine):
    """Spec 10, D2: create -> ~200 bars -> close -> A; delete order_state; rebuild -> B; A == B."""
    order_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30", "11:30", "13:00")
    projection_a = snapshot(engine, order_id)
    assert projection_a["status"] == "CLOSED"
    with engine.connect() as conn:
        processed = sum(int((s.bar_to - s.bar_from).total_seconds() // 60) + 1 for s in list_segments(conn, order_id))
    assert processed == 201  # all minutes 09:30-12:50 fall in one session

    rebuilt, projection_b = delete_and_rebuild(engine, order_id)
    assert projection_b == projection_a
    assert rebuilt == projection_a
    assert rebuild_projection(engine, order_id) == projection_a  # verify mode on the stored projection


def test_rebuild_is_immune_to_later_vendor_corrections(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    cycles(engine, source, "10:30", "11:30", "13:00")
    projection_a = snapshot(engine, order_id)
    source.load([raw(DAY, "10:05", 104, 104, 104, 104)])  # would remove the fill
    ingest_bars(engine, source, TICKER, et(DAY, "10:05"), et(DAY, "10:06"))
    assert delete_and_rebuild(engine, order_id)[1] == projection_a


def test_rebuild_interleaves_commands_and_segments(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    cycles(engine, source, "10:30")
    flag_order_review(engine, order_id, reason="MANUAL", ref="looked-odd")
    cycles(engine, source, "11:30", "11:30")
    cancel_order(engine, order_id, at=et(DAY, "11:45"))
    projection_a = snapshot(engine, order_id)
    with engine.connect() as conn:
        types = [e.prepared.type for e in stored_events(conn, order_id)]
    assert types == ["ORDER_CREATED", "FILLED", "NEEDS_REVIEW", "TARGET1_HIT", "CANCELED"]
    assert delete_and_rebuild(engine, order_id)[1] == projection_a


def test_rebuild_after_time_exit_with_stale_bar(engine):
    order_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30")
    finalize_validity(engine, order_id, now=AFTER_VALIDITY)
    projection_a = snapshot(engine, order_id)
    assert projection_a["status"] == "CLOSED" and projection_a["needs_review"]
    assert delete_and_rebuild(engine, order_id)[1] == projection_a


def test_rebuild_expired_pending_order(engine):
    order_id = submit_default(engine).auto_order_id
    finalize_validity(engine, order_id, now=AFTER_VALIDITY)
    projection_a = snapshot(engine, order_id)
    assert delete_and_rebuild(engine, order_id)[1] == projection_a


def test_rebuild_manual_order_with_inherited_state_and_audit_payload(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(
        [raw(DAY, "09:30", 99.5, 99.8, 99, 99.2)] + flat_raw(DAY, "09:31", "11:00", 99)
        + [raw(DAY, "11:00", 99.5, 100.8, 99.4, 100.5), raw(DAY, "11:01", 101, 101.5, 100.6, 101.2)]
        + flat_raw(DAY, "11:02", "11:10", 101)
    )
    created = create_manual_order(engine, signal_id, config=FillConfig(), code_version=CODE_VERSION,
                                  price_source=PRICE_SOURCE, created_at=et(DAY, "10:59", 30), gateway=feeds(source))
    cycles(engine, source, "11:05")
    projection_a = snapshot(engine, created.order_id)
    assert projection_a["status"] == "OPEN" and projection_a["entry_path"] == "RECLAIMED"
    assert delete_and_rebuild(engine, created.order_id)[1] == projection_a


def test_backdated_version_breaks_regeneration_and_freezes(engine):
    order_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30")
    with engine.connect() as conn:
        run = get_run(conn, list_segments(conn, order_id)[0].run_id)
    backdated_batch(engine, TICKER, [raw(DAY, "10:05", 104, 104, 104, 104)], run.data_as_of)

    with pytest.raises(errors.ProjectionIntegrityError) as caught:
        rebuild_projection(engine, order_id)
    assert caught.value.detail["reason"] == "SELECTED_DATA_HASH_MISMATCH"
    with engine.connect() as conn:
        incident = conn.execute(select(tables.integrity_incidents)).one()
        assert load_projection(conn, order_id).state.frozen
    assert incident.kind == errors.PROJECTION_INTEGRITY_ERROR and incident.order_id == order_id


def test_tampered_projection_is_detected(engine):
    order_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30", "11:30", "13:00")
    with engine.begin() as conn:
        conn.execute(text("UPDATE order_state SET r_multiple = 9 WHERE order_id = :id"), {"id": order_id})
    with pytest.raises(errors.ProjectionIntegrityError) as caught:
        rebuild_projection(engine, order_id)
    differences = caught.value.detail["differences"]
    assert set(differences) == {"r_multiple"}
    assert differences["r_multiple"]["rebuilt"] == Decimal("1.75")
    assert count(engine, "integrity_incidents") == 1


def test_rebuild_all_reports_each_order(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="other").auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30")
    assert rebuild_all_projections(engine) == {first: "OK", second: "OK"}
