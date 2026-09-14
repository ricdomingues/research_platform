from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    PRICE_SOURCE,
    FakeBarSource,
    FakeDividends,
    FakeReference,
    count,
    feeds,
    signal_body,
    submit_default,
)
from virtual_orders.evaluator.commands import cancel_order
from virtual_orders.evaluator.corporate import apply_dividends
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.evaluator.quality import run_end_of_day, run_session_quality
from virtual_orders.evaluator.signals import submit_signal
from virtual_orders.ledger.orders import read_projection_row

NAIVE = datetime(2025, 11, 25, 15, 0)  # noqa: DTZ001 - deliberately naive, to be rejected
SESSION = date(2025, 11, 25)


def test_live_cycle_rejects_naive_market_now_before_any_run(engine):
    submit_default(engine)
    source = FakeBarSource()
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=NAIVE)
    assert source.calls == []
    assert count(engine, "evaluation_runs") == 0


def test_session_quality_rejects_naive_market_now(engine):
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        run_session_quality(engine, session_day=SESSION, reference=FakeReference(), code_version=CODE_VERSION,
                            market_now=NAIVE)
    assert count(engine, "evaluation_runs") == 0


def test_end_of_day_rejects_naive_market_now_before_the_cycle(engine):
    submit_default(engine)
    source = FakeBarSource()
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        run_end_of_day(engine, gateway=feeds(source), reference=FakeReference(), session_day=SESSION,
                       code_version=CODE_VERSION, market_now=NAIVE)
    assert source.calls == []
    assert count(engine, "evaluation_runs") == 0


def test_manual_order_rejects_naive_created_at(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    with pytest.raises(ValueError, match="created_at must be timezone-aware"):
        create_manual_order(engine, signal_id, config=FillConfig(), code_version=CODE_VERSION,
                            price_source=PRICE_SOURCE, created_at=NAIVE)
    assert count(engine, "evaluation_runs") == 0


def test_manual_order_rejects_naive_created_at_before_signal_lookup(engine):
    with pytest.raises(ValueError, match="created_at must be timezone-aware"):
        create_manual_order(engine, uuid4(), config=FillConfig(), code_version=CODE_VERSION,
                            price_source=PRICE_SOURCE, created_at=NAIVE)


def test_cancel_rejects_naive_at(engine):
    order_id = submit_default(engine).auto_order_id
    with pytest.raises(ValueError, match="at must be timezone-aware"):
        cancel_order(engine, order_id, at=NAIVE)
    with engine.connect() as conn:
        assert read_projection_row(conn, order_id)["status"] == "PENDING"


def test_dividends_reject_naive_now(engine):
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        apply_dividends(engine, ex_date=date(2025, 11, 26), primary=FakeDividends("fmp"),
                        secondary=FakeDividends("yfinance"), tolerance=Decimal("0.001"), now=NAIVE)


def test_submit_signal_rejects_naive_now(engine):
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        submit_signal(engine, signal_body(), config=FillConfig(), code_version=CODE_VERSION,
                      price_source=PRICE_SOURCE, now=NAIVE)
    assert count(engine, "signals") == 0
