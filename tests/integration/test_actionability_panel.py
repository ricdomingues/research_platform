"""The panel read model against real Postgres (Plan 7, D103-D104).

Two properties matter more than the individual states, and both are here: the panel never claims a signal is
enterable where `POST /signals/{id}/orders` would answer 503, and reading the panel leaves no run behind.
"""


import pytest
from sqlalchemy import func, select

from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    TICKER,
    FakeBarSource,
    feeds,
    flat_raw,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import ACTIONABILITY_UNVERIFIABLE, ManualOrderError, create_manual_order
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.readmodels.actionability import COVERAGE_UNVERIFIED, PanelState, panel_rows
from virtual_orders.storage import tables

AS_OF = et(DAY, "13:00")
SINCE = et(DAY, "00:00")


def _store(engine, bars):
    ingest_bars(engine, FakeBarSource(bars), TICKER, et(DAY, "09:30"), AS_OF)


def _panel(engine, as_of=AS_OF):
    data_as_of = acquire_data_as_of(engine)
    with engine.connect() as conn:
        return panel_rows(conn, as_of=as_of, data_as_of=data_as_of, price_source=PRICE_SOURCE,
                          config=FillConfig(), since=SINCE)


def _manual(engine, signal_id, at=AS_OF):
    """The write path with no gateway: it decides on the very bars the panel just read."""
    return create_manual_order(engine, signal_id, config=FillConfig(), code_version=CODE_VERSION,
                               price_source=PRICE_SOURCE, created_at=at, gateway=None)


def _actionability_runs(engine):
    with engine.connect() as conn:
        return int(conn.execute(
            select(func.count()).select_from(tables.evaluation_runs)
            .where(tables.evaluation_runs.c.kind == "ACTIONABILITY")
        ).scalar_one())


def test_full_coverage_is_actionable_and_the_write_path_agrees(engine):
    signal_id = submit_default(engine).signal_id
    _store(engine, flat_raw(DAY, "09:30", "13:00", 105))

    (row,) = _panel(engine)

    assert row["signal_id"] == signal_id
    assert row["state"] == PanelState.ACTIONABLE.value
    assert _manual(engine, signal_id).order_id is not None  # the same bars, the same verdict


def test_a_hole_in_the_window_is_stale_and_the_write_path_refuses(engine):
    signal_id = submit_default(engine).signal_id
    # One expected minute is missing in the middle: STRICT_PRIMARY cannot verify the window.
    _store(engine, flat_raw(DAY, "09:30", "11:00", 105) + flat_raw(DAY, "11:02", "13:00", 105))

    (row,) = _panel(engine)

    assert row["state"] == PanelState.STALE.value
    assert row["reason"] == COVERAGE_UNVERIFIED
    with pytest.raises(ManualOrderError) as refused:
        _manual(engine, signal_id)
    assert refused.value.code == ACTIONABILITY_UNVERIFIABLE


def test_stale_wins_over_actionable_when_ingest_falls_behind(engine):
    """The rule the owner asked for by name, in its real failure mode: the worker stops, the clock does not.

    The same signal and the same stored bars read ACTIONABLE while the panel asks about the minute the data
    reaches, and STALE once it asks about a later one. Nothing about the setup changed -- only what the data
    can still answer for.
    """
    submit_default(engine)
    _store(engine, flat_raw(DAY, "09:30", "11:00", 105))

    assert _panel(engine, as_of=et(DAY, "11:00"))[0]["state"] == PanelState.ACTIONABLE.value
    assert _panel(engine, as_of=et(DAY, "13:00"))[0]["state"] == PanelState.STALE.value


def test_an_expired_signal_reads_expired_rather_than_stale(engine):
    """Expiry is decided before coverage, exactly as the write path decides it before ingest."""
    submit_default(engine, valid_sessions=1)
    _store(engine, flat_raw(DAY, "09:30", "13:00", 105))

    (row,) = _panel(engine, as_of=et("2025-11-28", "10:00"))

    assert row["state"] == PanelState.EXPIRED.value
    assert row["reason"] == "SIGNAL_EXPIRED"


def test_reading_the_panel_opens_no_run(engine):
    submit_default(engine)
    _store(engine, flat_raw(DAY, "09:30", "13:00", 105))
    before = _actionability_runs(engine)

    for _ in range(5):
        _panel(engine)

    assert _actionability_runs(engine) == before == 0


def test_a_closed_position_reads_exit_with_the_domain_close_reason(engine):
    """EXIT is read from the stored order, never from the hypothetical: it is about a position that exists."""
    submit_default(engine)
    run_live_cycle(engine, feeds(FakeBarSource(scenario_bars())), code_version=CODE_VERSION,
                   market_now=et(DAY, "13:00"))

    (row,) = _panel(engine)

    assert row["state"] == PanelState.EXIT.value
    assert row["reason"] == "TARGET_FINAL"
    assert row["order_id"] is not None
