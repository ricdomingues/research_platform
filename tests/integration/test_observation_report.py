from dataclasses import asdict, replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from core.domain.hashing import canonical_json
from core.domain.models import FillConfig
from tests.integration.observation_support import window_for
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    FakeBarSource,
    count,
    feeds,
    scenario_bars,
    signal_body,
    submit_default,
)
from tests.support import et
from virtual_orders.analytics.observation import Alignment, PressureBucket, Strength
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.signals import submit_signal
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation import (
    DEFINITIONS,
    REPORT_VERSION,
    build_observation_report,
    build_observation_summary,
    observation_snapshot,
    require_snapshot,
)
from virtual_orders.readmodels.observation_window import summary_sessions
from virtual_orders.storage import tables

NEXT = "2025-11-26"
HISTORY = ("evaluation_runs", "evaluation_run_status", "order_events", "bar_batches", "alert_outbox", "worker_sessions")


def two_sessions_with_one_trade_each(engine):
    source = FakeBarSource(scenario_bars() + scenario_bars(NEXT))
    submit_default(engine)
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    submit_signal(engine, signal_body(client_signal_id="next-day"), config=FillConfig(), code_version=CODE_VERSION,
                  price_source=PRICE_SOURCE, now=et(NEXT, "09:00"))
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(NEXT, hm))


def test_the_report_assembles_every_section_for_one_session_without_writing(engine):
    two_sessions_with_one_trade_each(engine)
    window = window_for(engine, date(2025, 11, 25))
    before = {name: count(engine, name) for name in HISTORY}

    with observation_snapshot(engine) as conn:
        report = build_observation_report(conn, window)

    assert {name: count(engine, name) for name in HISTORY} == before  # D60: reads only
    assert (report.report_version, report.session_day, report.complete) == (REPORT_VERSION, date(2025, 11, 25), True)
    assert (report.window_start, report.window_end, report.as_of) == (window.start, window.end, window.as_of)
    assert (report.session_open_utc, report.session_close_utc) == (et(DAY, "09:30"), et(DAY, "16:00"))
    assert report.provider_failures.by_run_kind[0].run_kind == "LIVE" and report.provider_failures.total_failures == 0
    assert (report.trades.closed, report.trades.stats.sum_r, report.latency.bar.median_seconds) == (
        1, Decimal("1.7500"), 3900)
    assert report.pressure.estimate is True
    assert set(report.definitions) == set(DEFINITIONS) == {
        "window", "provider_failures", "data_quality", "actionability", "rechecks", "alerts", "worker", "health",
        "trades", "latency", "pressure"}
    assert "estimate" in report.definitions["pressure"] and "never" in report.definitions["pressure"]
    assert canonical_json(asdict(report))  # every value is JSON-native after canonical normalization


def test_the_report_reads_one_read_only_repeatable_read_snapshot(engine):
    two_sessions_with_one_trade_each(engine)
    window = window_for(engine, date(2025, 11, 25))

    with engine.connect() as plain, pytest.raises(ValueError, match="REPEATABLE READ"):
        build_observation_report(plain, window)  # a plain READ COMMITTED connection is refused (D61)

    with observation_snapshot(engine) as conn:
        isolation = conn.execute(text("SHOW transaction_isolation")).scalar_one()
        read_only = conn.execute(text("SHOW transaction_read_only")).scalar_one()
        first = build_observation_report(conn, window)
        with engine.begin() as other:  # committed by another connection while the snapshot is open, in the window
            other.execute(tables.health_state_log.insert().values(
                state="DEGRADED", cause_codes=["LIVE_CYCLE_STALE"], observed_at=et(DAY, "14:00")))
        again = build_observation_report(conn, window)

    assert (isolation, read_only) == ("repeatable read", "on")
    assert again == first  # every section of both reads comes from the same snapshot
    with observation_snapshot(engine) as conn:
        later = build_observation_report(conn, window)
    assert (later.health.log_rows, later.health.transitions) == (first.health.log_rows + 1,
                                                                 first.health.transitions + 1)


def test_the_summary_aggregates_the_sessions_of_the_range(engine):
    two_sessions_with_one_trade_each(engine)
    windows = summary_sessions(date(2025, 11, 22), date(2025, 11, 26), acquire_data_as_of(engine))

    with observation_snapshot(engine) as conn:
        summary = build_observation_summary(conn, windows)

    assert (summary.first_day, summary.last_day, summary.sessions) == (date(2025, 11, 24), date(2025, 11, 26), 3)
    assert [(row.session_day, row.trades, row.fills, row.sum_r) for row in summary.days] == [
        (date(2025, 11, 24), 0, 0, Decimal("0.0000")),
        (date(2025, 11, 25), 1, 1, Decimal("1.7500")),
        (date(2025, 11, 26), 1, 1, Decimal("1.7500")),
    ]
    assert (summary.trades.trades, summary.trades.sum_r, summary.trades.mean_r) == (2, Decimal("3.5000"),
                                                                                   Decimal("1.7500"))
    assert (summary.latency.count, summary.latency.median_seconds, summary.excluded_needs_review) == (2, 3900, 0)
    # 2025-11-25: no stored bar before the 09:00 signal. 2025-11-26: the previous session's 12:21-12:50 bars.
    assert summary.pressure.buckets == (  # one trade each: counts and sum only, no mean below 5 trades
        PressureBucket(Alignment.NEUTRAL, Strength.WEAK, 1, 1, Decimal("1.7500"), None),
        PressureBucket(Alignment.UNAVAILABLE, None, 1, 1, Decimal("1.7500"), None),
    )
    assert summary.pressure.unavailable_reasons == {"NO_BARS": 1}
    assert summary.definitions == DEFINITIONS


def test_build_observation_summary_rejects_an_empty_window_sequence(engine):
    with observation_snapshot(engine) as conn, pytest.raises(
        ValueError, match="a summary needs at least one session window"
    ):
        build_observation_summary(conn, [])


def test_build_observation_summary_rejects_windows_that_do_not_share_one_as_of(engine):
    windows = summary_sessions(date(2025, 11, 24), date(2025, 11, 26), acquire_data_as_of(engine))
    mismatched = [windows[0], replace(windows[1], as_of=windows[1].as_of + timedelta(seconds=1))]

    with observation_snapshot(engine) as conn, pytest.raises(
        ValueError, match="a summary needs every window to share one as_of"
    ):
        build_observation_summary(conn, mismatched)


def test_build_observation_summary_rejects_windows_out_of_order(engine):
    windows = summary_sessions(date(2025, 11, 24), date(2025, 11, 26), acquire_data_as_of(engine))

    with observation_snapshot(engine) as conn, pytest.raises(
        ValueError, match="a summary needs its windows sorted by session_day"
    ):
        build_observation_summary(conn, list(reversed(windows)))


def test_require_snapshot_rejects_a_repeatable_read_connection_that_is_not_read_only(engine):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn, conn.begin():
        isolation = conn.execute(text("SHOW transaction_isolation")).scalar_one()
        read_only = conn.execute(text("SHOW transaction_read_only")).scalar_one()
        assert (isolation, read_only) == ("repeatable read", "off")  # REPEATABLE READ alone is not enough (D61)
        with pytest.raises(ValueError, match="REPEATABLE READ"):
            require_snapshot(conn)
