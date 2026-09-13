from uuid import uuid4

import pytest

from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    SIGNAL_CREATED_AT,
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
from virtual_orders.evaluator import replay
from virtual_orders.evaluator.commands import cancel_order
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.evaluator.replay import ReplayMode, ReplaySelectionError, reproduce_orders
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import get_order, load_projection, read_projection_row
from virtual_orders.ledger.runs import RunStatus, get_run, latest_run_status, list_segments
from virtual_orders.marketdata.ingest import ingest_bars


def cycles(engine, source, *hms):
    for hm in hms:
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))


def identities(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.identity() for e in stored_events(conn, order_id)]


def segment_facts(engine, order_id):
    with engine.connect() as conn:
        return [(s.run_id, s.bar_from, s.bar_to, s.selected_data_hash, s.first_seq, s.event_count)
                for s in list_segments(conn, order_id)]


def comparable_projection(engine, order_id):
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
    return {key: value for key, value in row.items() if key != "order_id"}


def test_reproduce_is_identical_after_vendor_correction_and_late_delivery(engine):
    source_id = submit_default(engine).auto_order_id
    original_bars = scenario_bars()
    source = FakeBarSource([b for b in original_bars if b.ts != et(DAY, "10:40")])
    cycles(engine, source, "10:30", "11:30", "13:00")

    source.load([raw(DAY, "10:05", 104, 104, 104, 104)])       # correction that would remove the fill
    source.load([b for b in original_bars if b.ts == et(DAY, "10:40")])  # minute delivered late
    ingest_bars(engine, source, TICKER, et(DAY, "09:30"), et(DAY, "13:00"))

    report = reproduce_orders(engine, code_version="replay-sha", order_ids=[source_id])
    assert report.mode is ReplayMode.REPRODUCE and report.failures == {}
    replay_id = report.created[source_id]
    assert identities(engine, replay_id) == identities(engine, source_id)
    assert segment_facts(engine, replay_id) == segment_facts(engine, source_id)
    assert comparable_projection(engine, replay_id) == comparable_projection(engine, source_id)
    with engine.connect() as conn:
        replay = get_order(conn, replay_id)
        assert get_run(conn, report.run_id).kind.value == "REPLAY"
    assert replay.replay and replay.replay_mode == "REPRODUCE" and replay.replay_of_order_id == source_id
    assert replay.code_version == "replay-sha" and replay.created_at == SIGNAL_CREATED_AT
    with engine.connect() as conn:
        assert rebuild_projection(engine, replay_id) == read_projection_row(conn, replay_id)


def test_reproduce_manual_order_with_commands(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(flat_raw(DAY, "09:30", "13:30", 105))
    created = create_manual_order(engine, signal_id, config=FillConfig(), code_version=CODE_VERSION,
                                  price_source=PRICE_SOURCE, created_at=et(DAY, "13:00", 5), gateway=feeds(source))
    cycles(engine, source, "13:20")
    cancel_order(engine, created.order_id, at=et(DAY, "13:21"))
    report = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[created.order_id])
    assert identities(engine, report.created[created.order_id]) == identities(engine, created.order_id)


def test_divergent_history_fails_records_incident_and_freezes_source(engine):
    source_id = submit_default(engine).auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30")
    with engine.connect() as conn:
        run = get_run(conn, list_segments(conn, source_id)[0].run_id)
    backdated_batch(engine, TICKER, [raw(DAY, "10:05", 104, 104, 104, 104)], run.data_as_of)

    report = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[source_id])
    assert report.created == {} and report.failures == {source_id: errors.REPRODUCE_DIVERGENCE}
    assert count(engine, "orders") == 1 and count(engine, "integrity_incidents") == 1
    with engine.connect() as conn:
        assert load_projection(conn, source_id).state.frozen


def test_selection_rules(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="second").auto_order_id
    with pytest.raises(ReplaySelectionError):
        reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[first],
                         created_from=et(DAY, "08:00"), created_to=et(DAY, "10:00"))
    with pytest.raises(ReplaySelectionError):
        reproduce_orders(engine, code_version=CODE_VERSION, created_from=et(DAY, "08:00"))
    report = reproduce_orders(engine, code_version=CODE_VERSION, created_from=et(DAY, "08:00"),
                              created_to=et(DAY, "10:00"))
    assert set(report.created) == {first, second}
    again = reproduce_orders(engine, code_version=CODE_VERSION, created_from=et(DAY, "08:00"),
                             created_to=et(DAY, "10:00"))
    assert set(again.created) == {first, second}  # replay orders themselves are never selected


def test_unknown_order_is_reported_not_raised(engine):
    missing = uuid4()
    report = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[missing])
    assert report.failures == {missing: "ORDER_NOT_FOUND"}


def test_replay_orders_are_not_evaluated_live(engine):
    source_id = submit_default(engine).auto_order_id
    bars = FakeBarSource(scenario_bars())
    cycles(engine, bars, "10:30")
    replay_id = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[source_id]).created[source_id]
    report = run_live_cycle(engine, feeds(bars), code_version=CODE_VERSION, market_now=et(DAY, "11:30"))
    assert [o.order_id for o in report.outcomes] == [source_id]
    assert identities(engine, replay_id)[-1][0] == "FILLED"


def test_unexpected_error_on_one_source_is_reported_and_others_still_replay(engine, monkeypatch):
    bad = submit_default(engine).auto_order_id
    good = submit_default(engine, client_signal_id="second").auto_order_id
    cycles(engine, FakeBarSource(scenario_bars()), "10:30", "11:30", "13:00")

    real_regenerate_history = replay.regenerate_history

    def failing_regenerate_history(conn, order):
        if order.id == bad:
            raise RuntimeError("boom")
        return real_regenerate_history(conn, order)

    monkeypatch.setattr(replay, "regenerate_history", failing_regenerate_history)

    report = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[bad, good])
    assert report.failures == {bad: "ERROR:RuntimeError"}
    assert good in report.created
    with engine.connect() as conn:
        status, detail = latest_run_status(conn, report.run_id)
    assert status == RunStatus.COMPLETED
    assert detail["failures"] == {str(bad): "ERROR:RuntimeError"}
