from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    FakeDividends,
    FakeSplits,
    dividend,
    feeds,
    flat_raw,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.corporate import apply_dividends, freeze_for_splits
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, read_projection_row
from virtual_orders.marketdata.sources import SplitRecord
from virtual_orders.storage import tables

EX_DAY = "2025-11-26"
TOLERANCE = Decimal("0.001")


def open_position(engine, **overrides):
    order_id = submit_default(engine, **overrides).auto_order_id
    run_live_cycle(engine, feeds(FakeBarSource(scenario_bars(ticker=overrides.get("ticker", "AAPL")))),
                   code_version=CODE_VERSION, market_now=et(DAY, "10:30"))
    return order_id


def pay(engine, fmp, yfinance, hm="09:25"):
    return apply_dividends(engine, ex_date=date.fromisoformat(EX_DAY), primary=fmp, secondary=yfinance,
                           tolerance=TOLERANCE, now=et(EX_DAY, hm))


def keys(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.event_key for e in stored_events(conn, order_id)]


def test_validated_dividend_is_credited_once(engine):
    order_id = open_position(engine)
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    yfinance = FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.2605")])
    assert [o.event_keys for o in pay(engine, fmp, yfinance)] == [(f"DIVIDEND:{EX_DAY}",)]
    assert [o.event_keys for o in pay(engine, fmp, yfinance)] == [()]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
        credited = stored_events(conn, order_id)[-1].prepared
        record = conn.execute(select(tables.dividends)).one()
    assert credited.payload["cash"] == "6.5" and row["r_multiple"] == Decimal("0.065")
    assert record.validated and record.amount == Decimal("0.26") and record.sources == ["fmp", "yfinance"]


@pytest.mark.parametrize("fmp, yfinance, sources", [
    (FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")]),
     FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.30")]), ["fmp", "yfinance"]),
    (FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")]), FakeDividends("yfinance", failing=True), ["fmp"]),
])
def test_unverified_dividend_flags_review_without_credit(engine, fmp, yfinance, sources):
    order_id = open_position(engine)
    assert [o.event_keys for o in pay(engine, fmp, yfinance)] == [(f"NEEDS_REVIEW:DIVIDEND_UNVERIFIED:{EX_DAY}",)]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
        record = conn.execute(select(tables.dividends)).one()
    assert row["r_multiple"] == 0 and row["needs_review"]
    assert not record.validated and record.sources == sources


def test_pending_orders_and_unrelated_tickers_are_untouched(engine):
    open_id = open_position(engine)
    pending_id = submit_default(engine, client_signal_id="pending", ticker="MSFT").auto_order_id
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26"), dividend("MSFT", EX_DAY, "0.91")])
    yfinance = FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26"), dividend("MSFT", EX_DAY, "0.91")])
    assert [o.order_id for o in pay(engine, fmp, yfinance)] == [open_id]
    assert keys(engine, pending_id) == ["ORDER_CREATED"]


def test_dividends_must_run_before_the_ex_date_open(engine):
    open_position(engine)
    with pytest.raises(ValueError, match="before the ex-date session opens"):
        pay(engine, FakeDividends("fmp"), FakeDividends("yfinance"), hm="09:30")


def test_rebuild_interleaves_dividend_between_sessions(engine):
    order_id = open_position(engine, target2=Decimal("150"), client_signal_id="long-hold")
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    pay(engine, fmp, FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")]))
    source = FakeBarSource(scenario_bars() + flat_raw(EX_DAY, "09:30", "10:00", 103))
    run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(EX_DAY, "10:00"))
    with engine.connect() as conn:
        projection_a = read_projection_row(conn, order_id)
        types = [e.prepared.type for e in stored_events(conn, order_id)]
    assert types == ["ORDER_CREATED", "FILLED", "DIVIDEND", "TARGET1_HIT"]
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert rebuild_projection(engine, order_id) == projection_a


def test_split_freezes_every_non_final_order_of_the_ticker(engine):
    open_id = open_position(engine)
    pending_id = submit_default(engine, client_signal_id="pending-aapl").auto_order_id
    other_id = submit_default(engine, client_signal_id="msft", ticker="MSFT").auto_order_id
    splits = FakeSplits([
        SplitRecord("AAPL", date(2025, 11, 26), Decimal(1), Decimal(4)),
        SplitRecord("AAPL", date(2025, 11, 20), Decimal(1), Decimal(2)),
    ])
    outcomes = {o.order_id: o.event_keys for o in freeze_for_splits(engine, splits, as_of_day=date(2025, 11, 26))}
    expected = ("FROZEN:SPLIT", "NEEDS_REVIEW:SPLIT:2025-11-26")
    assert outcomes == {open_id: expected, pending_id: expected}
    assert splits.calls == [(("AAPL", "MSFT"), date(2025, 11, 25), date(2025, 11, 26))]
    assert keys(engine, other_id) == ["ORDER_CREATED"]
    repeated = freeze_for_splits(engine, splits, as_of_day=date(2025, 11, 26))
    assert [o.event_keys for o in repeated] == [(), ()]
    report = run_live_cycle(engine, feeds(FakeBarSource(scenario_bars())), code_version=CODE_VERSION,
                            market_now=et(DAY, "11:30"))
    assert {o.order_id for o in report.outcomes} == {other_id}
