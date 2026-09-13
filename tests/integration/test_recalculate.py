from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    TICKER,
    FakeBarSource,
    count,
    feeds,
    flat_raw,
    raw,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.evaluator.replay import ReplayMode, ReplaySelectionError, recalculate_orders, reproduce_orders
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import get_order, read_projection_row
from virtual_orders.ledger.runs import RunStatus, latest_run_status, list_segments
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.storage import tables

EX_DAY = "2025-11-26"


def closed_order(engine, **overrides):
    order_id = submit_default(engine, **overrides).auto_order_id
    source = FakeBarSource(scenario_bars())
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id, source


def projection(engine, order_id):
    with engine.connect() as conn:
        return read_projection_row(conn, order_id)


def types(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.type for e in stored_events(conn, order_id)]


def test_recalculate_uses_corrected_history_without_touching_the_source(engine):
    source_id, source = closed_order(engine)
    before = projection(engine, source_id)
    source.load([raw(DAY, "10:05", 104, 104, 104, 104)])
    ingest_bars(engine, source, TICKER, et(DAY, "10:05"), et(DAY, "10:06"))

    report = recalculate_orders(engine, code_version="recalc-sha", order_ids=[source_id])
    assert report.mode is ReplayMode.RECALCULATE and report.failures == {}
    replay_id = report.created[source_id]
    with engine.connect() as conn:
        replay = get_order(conn, replay_id)
    assert replay.replay_mode == "RECALCULATE" and replay.market_data_snapshot_id is not None
    with engine.connect() as conn:
        assert replay.evaluation_start_ts == get_order(conn, source_id).evaluation_start_ts
    assert projection(engine, replay_id)["status"] == "EXPIRED"
    assert types(engine, replay_id) == ["ORDER_CREATED", "EXPIRED"]
    assert projection(engine, source_id) == before
    assert rebuild_projection(engine, replay_id) == projection(engine, replay_id)


def test_config_overrides_change_costs_and_are_recorded(engine):
    source_id, _ = closed_order(engine)
    report = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[source_id],
                                config_overrides={"commission_per_execution": Decimal("1"),
                                                  "risk_amount": Decimal("200")})
    replay_id = report.created[source_id]
    with engine.connect() as conn:
        replay = get_order(conn, replay_id)
    assert replay.config.commission_per_execution == 1 and replay.risk_amount == 200
    assert projection(engine, replay_id)["r_multiple"] == Decimal("1.735")
    assert types(engine, replay_id) == ["ORDER_CREATED", "FILLED", "TARGET1_HIT", "TARGET2_HIT"]


def test_snapshot_is_reused_for_same_data_as_of_and_new_after_correction(engine):
    source_id, source = closed_order(engine)
    as_of = acquire_data_as_of(engine)
    first = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[source_id], data_as_of=as_of)
    second = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[source_id], data_as_of=as_of)
    with engine.connect() as conn:
        snap_a = get_order(conn, first.created[source_id]).market_data_snapshot_id
        snap_b = get_order(conn, second.created[source_id]).market_data_snapshot_id
    assert snap_a == snap_b and count(engine, "market_data_snapshots") == 1

    source.load([raw(DAY, "10:05", 104, 104, 104, 104)])
    ingest_bars(engine, source, TICKER, et(DAY, "10:05"), et(DAY, "10:06"))
    third = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[source_id])
    with engine.connect() as conn:
        snap_c = get_order(conn, third.created[source_id]).market_data_snapshot_id
        manifests = conn.execute(select(tables.market_data_snapshots.c.content_manifest_hash)).scalars().all()
    assert snap_c != snap_a and len(set(manifests)) == 2


def test_dividends_are_interleaved_at_the_ex_date_open(engine):
    order_id = submit_default(engine, client_signal_id="long-hold", target2=Decimal("150")).auto_order_id
    source = FakeBarSource(scenario_bars() + flat_raw(EX_DAY, "09:30", "10:00", 103))
    run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, "16:30"))
    with engine.begin() as conn:
        conn.execute(tables.dividends.insert().values(
            ticker=TICKER, ex_date=date(2025, 11, 26), amount=Decimal("0.26"), pay_date=None,
            sources=["fmp", "yfinance"], validated=True, checked_at=et(EX_DAY, "09:25"),
        ))
    ingest_bars(engine, source, TICKER, et(EX_DAY, "09:30"), et(EX_DAY, "10:00"))

    replay_id = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    assert types(engine, replay_id) == [
        "ORDER_CREATED", "FILLED", "TARGET1_HIT", "DIVIDEND", "TIME_EXIT", "NEEDS_REVIEW",
    ]
    with engine.connect() as conn:
        dividend = [e.prepared for e in stored_events(conn, replay_id) if e.prepared.type == "DIVIDEND"][0]
        segments = list_segments(conn, replay_id)
    assert dividend.payload["cash"] == "3.25"
    assert [(s.bar_from, s.bar_to) for s in segments] == [
        (et(DAY, "09:30"), et(DAY, "15:59")), (et(EX_DAY, "09:30"), et("2025-11-28", "12:59")),
    ]
    assert rebuild_projection(engine, replay_id) == projection(engine, replay_id)


def test_missing_minute_is_missing_for_reproduce_but_present_for_recalculate(engine):
    """D6: absence is immutable inside the run that observed it, not in the database."""
    fill_bar = next(b for b in scenario_bars() if b.ts == et(DAY, "10:05"))
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource([b for b in scenario_bars() if b.ts != fill_bar.ts])
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    assert types(engine, order_id) == ["ORDER_CREATED"]

    source.load([fill_bar])  # provider delivers the 10:05 bar late
    ingest_bars(engine, source, TICKER, et(DAY, "10:05"), et(DAY, "10:06"))

    reproduced = reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    recalculated = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    assert types(engine, reproduced) == ["ORDER_CREATED"]
    with engine.connect() as conn:
        original_hashes = [s.selected_data_hash for s in list_segments(conn, order_id)]
        assert [s.selected_data_hash for s in list_segments(conn, reproduced)] == original_hashes
        filled = [e.prepared for e in stored_events(conn, recalculated) if e.prepared.type == "FILLED"]
    assert [e.bar_ts for e in filled] == [et(DAY, "10:05")]
    assert projection(engine, order_id)["status"] == "PENDING"


def test_selection_requires_ids_or_interval(engine):
    with pytest.raises(ReplaySelectionError):
        recalculate_orders(engine, code_version=CODE_VERSION)


def test_unexpected_error_on_one_source_is_reported_during_recalculate(engine, monkeypatch):
    bad, _ = closed_order(engine)
    good, _ = closed_order(engine, client_signal_id="second")

    import virtual_orders.evaluator.replay as replay_module
    real_regenerate_created = replay_module.regenerate_created

    def failing_regenerate_created(model, ctx, payload):
        if not failing_regenerate_created.done:
            failing_regenerate_created.done = True
            raise RuntimeError("boom")
        return real_regenerate_created(model, ctx, payload)

    failing_regenerate_created.done = False
    monkeypatch.setattr(replay_module, "regenerate_created", failing_regenerate_created)

    report = recalculate_orders(engine, code_version=CODE_VERSION, order_ids=[bad, good])
    assert report.failures == {bad: "ERROR:RuntimeError"}
    assert good in report.created
    with engine.connect() as conn:
        status, detail = latest_run_status(conn, report.run_id)
    assert status == RunStatus.COMPLETED
    assert detail["failures"] == {str(bad): "ERROR:RuntimeError"}
