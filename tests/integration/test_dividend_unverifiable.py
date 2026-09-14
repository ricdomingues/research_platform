from datetime import date
from decimal import Decimal

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    FakeDividends,
    count,
    feeds,
    flat_raw,
    raw,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.corporate import apply_dividends
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import delete_projection, read_projection_row

EX_DAY = "2025-11-26"
TOLERANCE = Decimal("0.001")
UNVERIFIED = f"NEEDS_REVIEW:DIVIDEND_UNVERIFIED:{EX_DAY}"


def held_open_bars():
    return flat_raw(DAY, "09:30", "10:05", 105) + [raw(DAY, "10:05", 101, 101.5, 100.5, 101.2)] + flat_raw(
        DAY, "10:06", "16:00", 103
    )


def evaluate_through_close(engine, bars):
    run_live_cycle(engine, feeds(FakeBarSource(bars)), code_version=CODE_VERSION, market_now=et(DAY, "16:30"),
                   close_trailing_gap=True)


def pay(engine, primary, secondary, failures=None):
    return apply_dividends(engine, ex_date=date.fromisoformat(EX_DAY), primary=primary, secondary=secondary,
                           tolerance=TOLERANCE, now=et(EX_DAY, "09:25"), source_failures=failures)


def both_failing():
    return FakeDividends("fmp", failing=True), FakeDividends("yfinance", failing=True)


def keys(engine, order_id):
    with engine.connect() as conn:
        return [e.prepared.event_key for e in stored_events(conn, order_id)]


def test_both_sources_raising_flags_an_open_position_without_a_dividend_record(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, held_open_bars())
    failures: dict[str, str] = {}

    outcomes = pay(engine, *both_failing(), failures=failures)

    assert [(o.order_id, o.event_keys) for o in outcomes] == [(order_id, (UNVERIFIED,))]
    assert set(failures) == {"fmp:AAPL", "yfinance:AAPL"}
    assert count(engine, "dividends") == 0
    with engine.connect() as conn:
        projection = read_projection_row(conn, order_id)
    assert projection["needs_review"] and projection["r_multiple"] == 0
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert rebuild_projection(engine, order_id) == projection


def test_rerun_with_sources_still_down_is_a_no_op(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, held_open_bars())
    pay(engine, *both_failing())
    assert [o.event_keys for o in pay(engine, *both_failing())] == [()]
    assert keys(engine, order_id).count(UNVERIFIED) == 1


def test_position_without_shares_is_not_flagged(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, flat_raw(DAY, "09:30", "16:00", 105))  # never enters the zone: no position
    assert [o.event_keys for o in pay(engine, *both_failing())] == [()]
    assert not any(key.startswith("NEEDS_REVIEW") for key in keys(engine, order_id))


def test_position_not_confirmed_through_the_prior_close_is_flagged_as_unconfirmed(engine):
    order_id = submit_default(engine).auto_order_id  # never evaluated
    assert [o.event_keys for o in pay(engine, *both_failing())] == [
        (f"NEEDS_REVIEW:DIVIDEND_POSITION_UNCONFIRMED:{EX_DAY}",)
    ]
    assert UNVERIFIED not in keys(engine, order_id)


def test_both_sources_answering_empty_is_still_no_dividend(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, held_open_bars())
    assert pay(engine, FakeDividends("fmp"), FakeDividends("yfinance")) == []
    assert not any(key.startswith("NEEDS_REVIEW") for key in keys(engine, order_id))


def test_one_source_raising_keeps_the_plan_2_behaviour(engine):
    order_id = submit_default(engine).auto_order_id
    evaluate_through_close(engine, held_open_bars())
    assert pay(engine, FakeDividends("fmp", failing=True), FakeDividends("yfinance")) == []
    assert UNVERIFIED not in keys(engine, order_id)
