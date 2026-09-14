from uuid import uuid4

from sqlalchemy import select, text

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    count,
    feeds,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator import rebuild as rebuild_module
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.rebuild import rebuild_all_projections
from virtual_orders.evaluator.replay import reproduce_orders
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import load_projection
from virtual_orders.storage import tables


def closed_order(engine):
    order_id = submit_default(engine).auto_order_id
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(FakeBarSource(scenario_bars())), code_version=CODE_VERSION,
                       market_now=et(DAY, hm))
    return order_id


def event_keys(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.event_key for e in stored_events(conn, order_id)]


def test_tampered_replay_projection_records_an_incident_without_freezing_the_replay(engine):
    source_id = closed_order(engine)
    replay_id = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[source_id]).created[source_id]
    before = event_keys(engine, replay_id)
    with engine.begin() as conn:
        conn.execute(text("UPDATE order_state SET r_multiple = 9 WHERE order_id = :id"), {"id": replay_id})

    assert rebuild_all_projections(engine, [replay_id]) == {replay_id: errors.PROJECTION_INTEGRITY_ERROR}

    with engine.connect() as conn:
        incident = conn.execute(select(tables.integrity_incidents)).one()
        assert not load_projection(conn, replay_id).state.frozen
    assert incident.order_id == replay_id and incident.kind == errors.PROJECTION_INTEGRITY_ERROR
    assert event_keys(engine, replay_id) == before  # no FROZEN / NEEDS_REVIEW appended to a replay (D15)


def test_tampered_live_projection_is_still_frozen(engine):
    order_id = submit_default(engine).auto_order_id
    run_live_cycle(engine, feeds(FakeBarSource(scenario_bars())), code_version=CODE_VERSION,
                   market_now=et(DAY, "10:30"))
    with engine.begin() as conn:
        conn.execute(text("UPDATE order_state SET qty_open = 9 WHERE order_id = :id"), {"id": order_id})
    assert rebuild_all_projections(engine, [order_id]) == {order_id: errors.PROJECTION_INTEGRITY_ERROR}
    assert "FROZEN:INTEGRITY" in event_keys(engine, order_id)


def test_rebuild_all_isolates_unexpected_errors_and_unknown_orders(engine, monkeypatch):
    broken = submit_default(engine).auto_order_id
    healthy = submit_default(engine, client_signal_id="other").auto_order_id
    original = rebuild_module.regenerate_history

    def exploding(conn, order):
        if order.id == broken:
            raise RuntimeError("boom")
        return original(conn, order)

    monkeypatch.setattr(rebuild_module, "regenerate_history", exploding)
    missing = uuid4()

    report = rebuild_all_projections(engine, [broken, healthy, missing])

    assert report == {broken: "ERROR:RuntimeError", healthy: "OK", missing: "ORDER_NOT_FOUND"}
    assert count(engine, "integrity_incidents") == 0


def test_rebuild_all_without_ids_still_scans_every_order(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="other").auto_order_id
    assert rebuild_all_projections(engine) == {first: "OK", second: "OK"}
