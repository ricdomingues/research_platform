from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    SIGNAL_CREATED_AT,
    FakeBarSource,
    FakeDividends,
    FakeSplits,
    count,
    dividend,
    feeds,
    flat_raw,
    raw,
    scenario_bars,
    signal_body,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.corporate import apply_dividends, freeze_for_splits
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.evaluator.signals import submit_signal
from virtual_orders.ledger import errors
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, get_order, read_projection_row
from virtual_orders.marketdata.sources import SplitRecord
from virtual_orders.storage import tables

EX_DAY = "2025-11-26"
TOLERANCE = Decimal("0.001")


def held_open_bars(day: str = DAY, ticker: str = "AAPL"):
    """Fills like `scenario_bars` at ~101, then flatlines at 103 (below target1) through the close."""
    return (
        flat_raw(day, "09:30", "10:05", 105, ticker)
        + [raw(day, "10:05", 101, 101.5, 100.5, 101.2, ticker=ticker)]
        + flat_raw(day, "10:06", "16:00", 103, ticker)
    )


def open_mid_session(engine, **overrides):
    """Evaluated only through 10:30: never reaches the prior session's close."""
    order_id = submit_default(engine, **overrides).auto_order_id
    run_live_cycle(engine, feeds(FakeBarSource(scenario_bars(ticker=overrides.get("ticker", "AAPL")))),
                   code_version=CODE_VERSION, market_now=et(DAY, "10:30"))
    return order_id


def open_confirmed(engine, **overrides):
    """Evaluated all the way through the session close: a candidate whose qty_open can be trusted."""
    order_id = submit_default(engine, **overrides).auto_order_id
    ticker = overrides.get("ticker", "AAPL")
    run_live_cycle(engine, feeds(FakeBarSource(held_open_bars(ticker=ticker))), code_version=CODE_VERSION,
                   market_now=et(DAY, "16:30"), close_trailing_gap=True)
    return order_id


def pay(engine, fmp, yfinance, hm="09:25"):
    return apply_dividends(engine, ex_date=date.fromisoformat(EX_DAY), primary=fmp, secondary=yfinance,
                           tolerance=TOLERANCE, now=et(EX_DAY, hm))


def keys(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.event_key for e in stored_events(conn, order_id)]


def test_validated_dividend_is_credited_once(engine):
    order_id = open_confirmed(engine)
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
    order_id = open_confirmed(engine)
    assert [o.event_keys for o in pay(engine, fmp, yfinance)] == [(f"NEEDS_REVIEW:DIVIDEND_UNVERIFIED:{EX_DAY}",)]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
        record = conn.execute(select(tables.dividends)).one()
    assert row["r_multiple"] == 0 and row["needs_review"]
    assert not record.validated and record.sources == sources


def test_rerun_after_the_missing_source_recovers_keeps_the_first_record(engine):
    order_id = open_confirmed(engine)
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    first_outcomes = pay(engine, fmp, FakeDividends("yfinance", failing=True))
    assert [o.event_keys for o in first_outcomes] == [(f"NEEDS_REVIEW:DIVIDEND_UNVERIFIED:{EX_DAY}",)]
    recovered = FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")])
    second_outcomes = pay(engine, fmp, recovered)
    assert [o.event_keys for o in second_outcomes] == [()]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
        record = conn.execute(select(tables.dividends)).one()
    assert row["r_multiple"] == 0 and row["needs_review"]
    assert not record.validated and record.sources == ["fmp"]


def test_order_tolerance_mismatch_flags_review_without_credit(engine):
    submission = submit_signal(
        engine, signal_body(client_signal_id="tight-tolerance"),
        config=FillConfig(dividend_tolerance=Decimal("0.01")),
        code_version=CODE_VERSION, price_source=PRICE_SOURCE, now=SIGNAL_CREATED_AT,
    )
    order_id = submission.auto_order_id
    run_live_cycle(engine, feeds(FakeBarSource(held_open_bars())), code_version=CODE_VERSION,
                   market_now=et(DAY, "16:30"), close_trailing_gap=True)
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    yfinance = FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")])
    outcomes = pay(engine, fmp, yfinance)
    assert [o.event_keys for o in outcomes] == [(f"NEEDS_REVIEW:DIVIDEND_TOLERANCE_MISMATCH:{EX_DAY}",)]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
        record = conn.execute(select(tables.dividends)).one()
    assert row["r_multiple"] == 0 and row["needs_review"]
    assert record.validated  # sources agree exactly; only this order's own tolerance differs from the job's


def test_position_not_evaluated_through_the_prior_close_flags_review_without_credit(engine):
    order_id = open_mid_session(engine)
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    yfinance = FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")])
    outcomes = pay(engine, fmp, yfinance)
    assert [o.event_keys for o in outcomes] == [(f"NEEDS_REVIEW:DIVIDEND_POSITION_UNCONFIRMED:{EX_DAY}",)]
    with engine.connect() as conn:
        row = read_projection_row(conn, order_id)
    assert row["r_multiple"] == 0 and row["needs_review"]


def test_order_evaluated_from_the_ex_date_open_is_not_a_dividend_candidate(engine):
    open_id = open_confirmed(engine)
    late = submit_signal(
        engine, signal_body(client_signal_id="after-close"), config=FillConfig(),
        code_version=CODE_VERSION, price_source=PRICE_SOURCE, now=et(DAY, "16:30"),
    )
    with engine.connect() as conn:
        assert get_order(conn, late.auto_order_id).evaluation_start_ts == et(EX_DAY, "09:30")
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    yfinance = FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")])
    assert [o.order_id for o in pay(engine, fmp, yfinance)] == [open_id]
    assert keys(engine, late.auto_order_id) == ["ORDER_CREATED"]


def test_pending_orders_and_unrelated_tickers_are_untouched(engine):
    open_id = open_confirmed(engine)
    pending_id = submit_default(engine, client_signal_id="pending", ticker="MSFT").auto_order_id
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    yfinance = FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")])
    assert [o.order_id for o in pay(engine, fmp, yfinance)] == [open_id]
    assert keys(engine, pending_id) == ["ORDER_CREATED"]


def test_dividends_must_run_before_the_ex_date_open(engine):
    open_confirmed(engine)
    with pytest.raises(ValueError, match="before the ex-date session opens"):
        pay(engine, FakeDividends("fmp"), FakeDividends("yfinance"), hm="09:30")


def test_dividends_must_run_after_the_previous_session_close(engine):
    open_confirmed(engine)
    fmp, yfinance = FakeDividends("fmp"), FakeDividends("yfinance")
    with pytest.raises(ValueError, match="at or after the previous session"):
        apply_dividends(engine, ex_date=date.fromisoformat(EX_DAY), primary=fmp, secondary=yfinance,
                        tolerance=TOLERANCE, now=et(DAY, "15:00"))


def test_rebuild_interleaves_dividend_between_sessions(engine):
    order_id = open_confirmed(engine, target2=Decimal("150"), client_signal_id="long-hold")
    fmp = FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")])
    pay(engine, fmp, FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")]))
    source = FakeBarSource(
        held_open_bars()
        + flat_raw(EX_DAY, "09:30", "09:59", 103)
        + [raw(EX_DAY, "09:59", 105, 106.5, 104.8, 106)]
    )
    run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(EX_DAY, "10:00"))
    with engine.connect() as conn:
        projection_a = read_projection_row(conn, order_id)
        types = [e.prepared.type for e in stored_events(conn, order_id)]
    assert types == ["ORDER_CREATED", "FILLED", "DIVIDEND", "TARGET1_HIT"]
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert rebuild_projection(engine, order_id) == projection_a


def test_split_freezes_every_non_final_order_of_the_ticker(engine):
    open_id = open_mid_session(engine)
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


def test_split_quarantines_projection_less_order_without_stopping_others(engine):
    broken_id = open_mid_session(engine)
    healthy_id = submit_default(engine, client_signal_id="pending-aapl").auto_order_id
    with engine.begin() as conn:
        delete_projection(conn, broken_id)
    splits = FakeSplits([SplitRecord("AAPL", date(2025, 11, 26), Decimal(1), Decimal(4))])
    outcomes = {o.order_id: o for o in freeze_for_splits(engine, splits, as_of_day=date(2025, 11, 26))}
    assert outcomes[broken_id].error == errors.PROJECTION_MISSING
    assert outcomes[healthy_id].event_keys == ("FROZEN:SPLIT", "NEEDS_REVIEW:SPLIT:2025-11-26")
    assert count(engine, "integrity_incidents") == 1
