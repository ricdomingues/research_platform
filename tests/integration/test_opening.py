from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    FakeDividends,
    FakeSplits,
    count,
    dividend,
    feeds,
    flat_raw,
    raw,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.opening import run_opening
from virtual_orders.ledger.runs import RunKind, RunStatus, get_run, latest_run_status
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.marketdata.sources import SourceUnavailable
from virtual_orders.storage import tables

EX_DAY = "2025-11-26"
TOLERANCE = Decimal("0.001")


class FailingSplits:
    def fetch_splits(self, tickers, start, end):
        raise SourceUnavailable("splits unavailable (fake)")


def open_confirmed(engine):
    """Same scenario as test_corporate.open_confirmed: filled at ~101 and evaluated through the close."""
    order_id = submit_default(engine).auto_order_id
    bars = (
        flat_raw(DAY, "09:30", "10:05", 105)
        + [raw(DAY, "10:05", 101, 101.5, 100.5, 101.2)]
        + flat_raw(DAY, "10:06", "16:00", 103)
    )
    run_live_cycle(engine, feeds(FakeBarSource(bars)), code_version=CODE_VERSION, market_now=et(DAY, "16:30"),
                   close_trailing_gap=True)
    return order_id


def opening(engine, primary, secondary, split_source, hm="09:25"):
    return run_opening(engine, session_day=date.fromisoformat(EX_DAY), primary=primary, secondary=secondary,
                       split_source=split_source, tolerance=TOLERANCE, code_version=CODE_VERSION,
                       now=et(EX_DAY, hm))


def test_opening_run_records_sources_and_outcomes(engine):
    open_confirmed(engine)
    report = opening(engine, FakeDividends("fmp", [dividend("AAPL", EX_DAY, "0.26")]),
                     FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.2605")]), FakeSplits())

    assert [o.event_keys for o in report.dividends] == [(f"DIVIDEND:{EX_DAY}",)]
    assert report.splits == () and report.source_failures == {}
    with engine.connect() as conn:
        run = get_run(conn, report.run_id)
        status, detail = latest_run_status(conn, report.run_id)
    assert run.kind is RunKind.OPENING and status is RunStatus.COMPLETED
    assert detail["session_day"] == EX_DAY
    assert detail["dividend_sources"] == ["fmp", "yfinance"] and detail["split_source"] == "FakeSplits"
    assert detail["dividend_orders"] == 1 and detail["split_orders"] == 0
    assert detail["source_failures"] == {}
    assert detail["dividend_order_errors"] == {} and detail["split_integrity_errors"] == {}


def test_source_failures_are_recorded_instead_of_silent(engine):
    open_confirmed(engine)
    report = opening(engine, FakeDividends("fmp", failing=True),
                     FakeDividends("yfinance", [dividend("AAPL", EX_DAY, "0.26")]), FailingSplits())

    assert report.source_failures == {"fmp:AAPL": "fmp unavailable (fake)", "splits": "splits unavailable (fake)"}
    with engine.connect() as conn:
        status, detail = latest_run_status(conn, report.run_id)
    assert status is RunStatus.COMPLETED
    assert detail["source_failures"] == report.source_failures


def test_opening_outside_the_window_creates_no_run(engine):
    with pytest.raises(ValueError, match="before the ex-date session opens"):
        opening(engine, FakeDividends("fmp"), FakeDividends("yfinance"), FakeSplits(), hm="09:45")
    assert count(engine, "evaluation_runs") == 0


def test_run_kind_check_accepts_opening_and_rejects_unknown_kinds(engine):
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        conn.execute(tables.evaluation_runs.insert().values(
            run_id=uuid4(), kind="OPENING", data_as_of=as_of, code_version=CODE_VERSION, started_at=as_of))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.evaluation_runs.insert().values(
                run_id=uuid4(), kind="NOPE", data_as_of=as_of, code_version=CODE_VERSION, started_at=as_of))


class ExplodingDividends:
    name = "exploding"

    def fetch_dividends(self, ticker, start, end):
        raise RuntimeError("programming error (fake)")


def test_unexpected_error_marks_the_opening_run_failed(engine):
    open_confirmed(engine)
    with pytest.raises(RuntimeError):
        opening(engine, ExplodingDividends(), FakeDividends("yfinance"), FakeSplits())
    with engine.connect() as conn:
        run_id = conn.execute(
            select(tables.evaluation_runs.c.run_id).where(tables.evaluation_runs.c.kind == "OPENING")
        ).scalar_one()
        status, detail = latest_run_status(conn, run_id)
    assert status is RunStatus.FAILED
    assert detail["session_day"] == EX_DAY and detail["error"].startswith("RuntimeError")
