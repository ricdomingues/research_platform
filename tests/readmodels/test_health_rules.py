from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from virtual_orders.readmodels.health import (
    EXPECTED_SCHEMA_REVISION,
    HealthSnapshot,
    HealthState,
    RunSummary,
    Severity,
    database_unavailable,
    evaluate_health,
    schema_not_at_head,
)
from virtual_orders.readmodels.incidents import IncidentGroup

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2025, 11, 25, 15, 30, tzinfo=UTC)  # 10:30 ET
SESSION_OPEN = datetime(2025, 11, 25, 14, 30, tzinfo=UTC)  # 09:30 ET


def run(kind="LIVE", status="COMPLETED", minutes_ago=1, **detail) -> RunSummary:
    started = NOW - timedelta(minutes=minutes_ago)
    return RunSummary(f"{kind}-{minutes_ago}", kind, status, started, started,
                      {"market_now": started.isoformat(), **detail})


def snapshot(**overrides) -> HealthSnapshot:
    base = HealthSnapshot(
        now=NOW, session_open_utc=None, live_runs=(), last_end_of_day=None, last_opening=None,
        latest_ingested_at=None, incident_groups=(), incidents_total=0, frozen_orders=0,
        orders_without_projection=0, orders_without_projection_ids=(), needs_review={}, actionability={},
    )
    return replace(base, **overrides)


def codes(report):
    return {cause.code: cause.severity for cause in report.causes}


def test_expected_schema_revision_is_the_migration_head():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == EXPECTED_SCHEMA_REVISION


def test_nothing_to_report_is_healthy():
    report = evaluate_health(snapshot(), eval_interval_minutes=2)
    assert report.state is HealthState.HEALTHY and report.causes == ()


def test_only_an_unreachable_database_or_a_schema_off_head_is_unhealthy():
    down = database_unavailable(ConnectionError("refused"))
    assert down.state is HealthState.UNHEALTHY and [c.code for c in down.causes] == ["DATABASE_UNAVAILABLE"]
    behind = schema_not_at_head("0001")
    assert behind.state is HealthState.UNHEALTHY and behind.snapshot is None
    assert behind.causes[0].detail == {"expected": EXPECTED_SCHEMA_REVISION, "found": "0001"}

    failing_runs = tuple(
        run(minutes_ago=m, ingest_failures={"nope:AAPL": "UNKNOWN_DATA_SOURCE: no bar source registered"},
            order_errors={"o1": "ERROR:OperationalError", "o2": "ERROR:KeyError"})
        for m in (32, 34, 36)
    )
    worst = evaluate_health(snapshot(
        session_open_utc=SESSION_OPEN,
        live_runs=(run(status="FAILED", minutes_ago=30, error="boom"), *failing_runs),
        last_end_of_day=run(kind="END_OF_DAY", not_evaluated={"o3": "PROVIDER_FAILURE"}, session_day="2025-11-24"),
        last_opening=run(kind="OPENING", status="FAILED", error="boom"),
        incident_groups=(IncidentGroup("EVENT_HASH_CONFLICT", None, 5, 1, ("a",), NOW, NOW),), incidents_total=5,
        frozen_orders=1, orders_without_projection=1, orders_without_projection_ids=("b",),
        needs_review={"MISSING_BAR": 1}, actionability={"ACTIONABILITY_UNVERIFIABLE": 1},
    ), eval_interval_minutes=2)
    assert worst.state is HealthState.DEGRADED
    assert Severity.UNHEALTHY not in {cause.severity for cause in worst.causes}
    assert {
        "LAST_CYCLE_FAILED", "UNKNOWN_DATA_SOURCE", "INGEST_FAILURES_CONSECUTIVE", "LIVE_CYCLE_STALE",
        "INTEGRITY_INCIDENTS", "PROJECTION_MISSING_ORDERS", "INFRASTRUCTURE_ERRORS", "ORDER_ERRORS",
        "FROZEN_ORDERS", "QUALITY_NOT_EVALUATED", "OPENING_SOURCE_FAILURES",
    } <= {cause.code for cause in worst.causes}


def test_last_cycle_failed_is_degraded():
    report = evaluate_health(snapshot(live_runs=(run(status="FAILED", error="boom"),)), eval_interval_minutes=2)
    assert report.state is HealthState.DEGRADED and codes(report) == {"LAST_CYCLE_FAILED": Severity.DEGRADED}


def test_provider_failures_degrade_after_three_consecutive_cycles_and_are_informational_before():
    failing = {"ingest_failures": {"alpaca_iex:AAPL": "down"}}
    three = tuple(run(minutes_ago=m, **failing) for m in (1, 3, 5))
    report = evaluate_health(snapshot(live_runs=three), eval_interval_minutes=2)
    assert codes(report) == {"INGEST_FAILURES_CONSECUTIVE": Severity.DEGRADED}
    assert report.causes[0].detail == {"feeds": {"alpaca_iex:AAPL": 3}}
    assert report.state is HealthState.DEGRADED

    two = tuple(run(minutes_ago=m, **failing) for m in (1, 3))
    report = evaluate_health(snapshot(live_runs=two), eval_interval_minutes=2)
    assert codes(report) == {"INGEST_FAILURES": Severity.INFO} and report.state is HealthState.HEALTHY
    assert report.causes[0].detail == {"feeds": {"alpaca_iex:AAPL": 2}, "threshold": 3}

    interrupted = (run(minutes_ago=1, **failing), run(minutes_ago=3, ingest_failures={}), run(minutes_ago=5, **failing))
    assert codes(evaluate_health(snapshot(live_runs=interrupted), eval_interval_minutes=2)) == {
        "INGEST_FAILURES": Severity.INFO}


def test_unknown_data_source_is_degraded():
    live = run(ingest_failures={"nope:AAPL": "UNKNOWN_DATA_SOURCE: no bar source registered for 'nope'"})
    report = evaluate_health(snapshot(live_runs=(live,)), eval_interval_minutes=2)
    assert codes(report)["UNKNOWN_DATA_SOURCE"] is Severity.DEGRADED
    assert report.state is HealthState.DEGRADED


def test_stale_cycle_counts_only_during_a_session():
    during = {"session_open_utc": SESSION_OPEN}
    assert codes(evaluate_health(snapshot(**during, live_runs=(run(minutes_ago=7),)), eval_interval_minutes=2)) == {
        "LIVE_CYCLE_STALE": Severity.DEGRADED}
    assert evaluate_health(snapshot(**during, live_runs=(run(minutes_ago=5),)), eval_interval_minutes=2).causes == ()
    assert codes(evaluate_health(snapshot(**during), eval_interval_minutes=2)) == {
        "LIVE_CYCLE_STALE": Severity.DEGRADED}
    assert evaluate_health(snapshot(live_runs=(run(minutes_ago=7),)), eval_interval_minutes=2).causes == ()


def test_the_session_open_is_not_stale_because_of_the_previous_day_cycle():
    yesterday = run(minutes_ago=60 * 18)  # previous session, 16:30 ET
    just_opened = snapshot(now=SESSION_OPEN + timedelta(minutes=1), session_open_utc=SESSION_OPEN,
                           live_runs=(yesterday,))
    assert evaluate_health(just_opened, eval_interval_minutes=2).causes == ()
    later = replace(just_opened, now=SESSION_OPEN + timedelta(minutes=7))
    assert codes(evaluate_health(later, eval_interval_minutes=2)) == {"LIVE_CYCLE_STALE": Severity.DEGRADED}


def test_incidents_frozen_orders_and_order_errors_degrade():
    group = IncidentGroup("PROJECTION_MISSING", None, 3, 2, ("a", "b"), NOW, NOW)
    live = run(order_errors={"o1": "ERROR:KeyError"})
    eod = run(kind="END_OF_DAY", order_errors={"o2": "ERROR:OperationalError"},
              not_evaluated={"o3": "PROVIDER_FAILURE", "o4": "NO_OBSERVATIONS", "o5": "PROVIDER_FAILURE"},
              session_day="2025-11-24")
    report = evaluate_health(snapshot(incident_groups=(group,), incidents_total=3, frozen_orders=2,
                                      live_runs=(live,), last_end_of_day=eod), eval_interval_minutes=2)
    assert codes(report) == {
        "INTEGRITY_INCIDENTS": Severity.DEGRADED, "INFRASTRUCTURE_ERRORS": Severity.DEGRADED,
        "ORDER_ERRORS": Severity.DEGRADED, "FROZEN_ORDERS": Severity.DEGRADED,
        "QUALITY_NOT_EVALUATED": Severity.DEGRADED,
    }
    by_code = {cause.code: cause for cause in report.causes}
    assert by_code["QUALITY_NOT_EVALUATED"].detail == {
        "session_day": "2025-11-24", "reasons": {"NO_OBSERVATIONS": 1, "PROVIDER_FAILURE": 2}}
    assert by_code["ORDER_ERRORS"].detail["groups"][0].error_type == "KeyError"
    assert by_code["INTEGRITY_INCIDENTS"].detail["total"] == 3
    assert report.state is HealthState.DEGRADED


def test_orders_without_projection_are_reported_and_count_as_frozen():
    report = evaluate_health(snapshot(frozen_orders=1, orders_without_projection=2,
                                      orders_without_projection_ids=("a", "b")), eval_interval_minutes=2)
    by_code = {cause.code: cause for cause in report.causes}
    assert by_code["PROJECTION_MISSING_ORDERS"].detail == {"count": 2, "orders": ["a", "b"]}
    assert by_code["FROZEN_ORDERS"].detail == {"count": 3, "frozen_projections": 1, "without_projection": 2}
    assert report.state is HealthState.DEGRADED


def test_incidents_outside_the_window_stay_visible_as_history():
    report = evaluate_health(snapshot(incidents_total=4), eval_interval_minutes=2)
    assert codes(report) == {"INTEGRITY_INCIDENTS_HISTORY": Severity.INFO}
    assert report.causes[0].detail == {"total": 4} and report.state is HealthState.HEALTHY


def test_opening_failures_degrade():
    opening = run(kind="OPENING", source_failures={"fmp:AAPL": "down"},
                  dividend_order_errors={}, split_order_errors={})
    assert codes(evaluate_health(snapshot(last_opening=opening), eval_interval_minutes=2)) == {
        "OPENING_SOURCE_FAILURES": Severity.DEGRADED}
    failed = run(kind="OPENING", status="FAILED", error="boom")
    assert codes(evaluate_health(snapshot(last_opening=failed), eval_interval_minutes=2)) == {
        "OPENING_SOURCE_FAILURES": Severity.DEGRADED}


def test_review_queue_and_unverifiable_actionability_are_informational():
    report = evaluate_health(snapshot(needs_review={"MISSING_BAR": 4}, actionability={"ACTIONABILITY_UNVERIFIABLE": 2,
                                                                                      "ACTIONABLE": 5}),
                             eval_interval_minutes=2)
    assert report.state is HealthState.HEALTHY
    assert codes(report) == {"NEEDS_REVIEW_QUEUE": Severity.INFO, "ACTIONABILITY_UNVERIFIABLE": Severity.INFO}
