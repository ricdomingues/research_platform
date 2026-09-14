from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy.exc import OperationalError

from virtual_orders.readmodels import health as health_module
from virtual_orders.readmodels.health import (
    UNSPECIFIED_REVIEW_REASON,
    Cause,
    HealthSnapshot,
    HealthState,
    RunSummary,
    Severity,
    evaluate_health,
    health_query_timeout,
    is_statement_timeout,
)

NOW = datetime(2025, 11, 25, 13, 0, tzinfo=UTC)  # 08:00 ET, no session


def snapshot(**overrides) -> HealthSnapshot:
    base = HealthSnapshot(
        now=NOW, session_open_utc=None, live_runs=(), last_end_of_day=None, last_opening=None,
        latest_ingested_at=None, incident_groups=(), incidents_total=0, frozen_orders=0,
        orders_without_projection=0, orders_without_projection_ids=(), needs_review={}, actionability={},
    )
    return replace(base, **overrides)


def live(**detail) -> RunSummary:
    return RunSummary("LIVE-1", "LIVE", "COMPLETED", NOW, NOW, {"market_now": NOW.isoformat(), **detail})


class CanceledStatement(Exception):
    sqlstate = "57014"


class ConnectionLost(Exception):
    sqlstate = "08006"


def test_per_order_maps_and_feed_lists_in_facts_are_capped_but_causes_see_everything():
    errors = {f"order-{i:03d}": "ERROR:KeyError" for i in range(250)}
    feeds = {f"src:T{i:03d}": "down" for i in range(150)}
    report = evaluate_health(snapshot(live_runs=(live(order_errors=errors, ingest_failures=feeds),)),
                             eval_interval_minutes=2)
    assert report.snapshot is not None
    detail = report.snapshot.live_runs[0].detail
    assert list(detail["order_errors"]) == sorted(errors)[:100] and detail["order_errors_total"] == 250
    assert detail["ingest_failures"] == sorted(feeds)[:100] and detail["ingest_failures_total"] == 150
    group = next(c for c in report.causes if c.code == "ORDER_ERRORS").detail["groups"][0]
    assert group.occurrences == 250 and group.affected_count == 250


def test_small_maps_are_unchanged_and_have_no_total():
    report = evaluate_health(snapshot(live_runs=(live(order_errors={"o1": "ERROR:KeyError"}),)),
                             eval_interval_minutes=2)
    assert report.snapshot is not None
    detail = report.snapshot.live_runs[0].detail
    assert detail["order_errors"] == {"o1": "ERROR:KeyError"} and "order_errors_total" not in detail


def test_collected_quality_pending_replaces_the_last_end_of_day_view():
    eod = RunSummary("EOD-1", "END_OF_DAY", "COMPLETED", NOW, NOW,
                     {"session_day": "2025-11-24", "not_evaluated": {"o1": "PROVIDER_FAILURE"}})
    consumed = evaluate_health(snapshot(last_end_of_day=eod, quality_pending={}), eval_interval_minutes=2)
    assert "QUALITY_NOT_EVALUATED" not in {c.code for c in consumed.causes}

    pending = evaluate_health(snapshot(last_end_of_day=eod, quality_pending={
        "o1:2025-11-21": "NO_OBSERVATIONS", "o2:2025-11-24": "PROVIDER_FAILURE",
    }), eval_interval_minutes=2)
    cause = next(c for c in pending.causes if c.code == "QUALITY_NOT_EVALUATED")
    assert cause.severity is Severity.DEGRADED
    assert cause.detail == {"session_days": ["2025-11-21", "2025-11-24"], "count": 2,
                            "reasons": {"NO_OBSERVATIONS": 1, "PROVIDER_FAILURE": 1}}


def test_quality_pending_in_facts_is_capped():
    pending = {f"order-{i:03d}:2025-11-24": "PROVIDER_FAILURE" for i in range(120)}
    report = evaluate_health(snapshot(quality_pending=pending), eval_interval_minutes=2)
    assert report.snapshot is not None and report.snapshot.quality_pending is not None
    assert len(report.snapshot.quality_pending) == 100
    assert next(c for c in report.causes if c.code == "QUALITY_NOT_EVALUATED").detail["count"] == 120


def test_statement_timeout_is_recognized_from_the_driver_sqlstate():
    assert is_statement_timeout(OperationalError("SELECT pg_sleep(1)", {}, CanceledStatement()))
    assert not is_statement_timeout(OperationalError("SELECT 1", {}, ConnectionLost()))
    assert not is_statement_timeout(ValueError("no orig"))
    report = health_query_timeout(OperationalError("SELECT pg_sleep(1)", {}, CanceledStatement()), 50)
    assert report.state is HealthState.DEGRADED and report.snapshot is None
    assert report.causes == (Cause("HEALTH_QUERY_TIMEOUT", Severity.DEGRADED,
                                   {"error": "OperationalError", "timeout_ms": 50}),)


def test_missing_opening_and_end_of_day_runs_degrade_with_their_session_days():
    missing = {"OPENING": ["2025-11-26"], "END_OF_DAY": ["2025-11-24", "2025-11-26"]}
    report = evaluate_health(snapshot(missing_runs=missing), eval_interval_minutes=2)
    causes = {cause.code: cause for cause in report.causes}
    assert causes["OPENING_MISSING"] == Cause("OPENING_MISSING", Severity.DEGRADED, {"session_days": ["2025-11-26"]})
    assert causes["END_OF_DAY_MISSING"] == Cause("END_OF_DAY_MISSING", Severity.DEGRADED,
                                                 {"session_days": ["2025-11-24", "2025-11-26"]})
    assert report.state is HealthState.DEGRADED
    assert report.snapshot is not None and report.snapshot.missing_runs == missing
    assert evaluate_health(snapshot(missing_runs={}), eval_interval_minutes=2).state is HealthState.HEALTHY


def test_unspecified_literal_matches_the_constant():
    assert f"'{UNSPECIFIED_REVIEW_REASON}'" in str(health_module._REVIEW_REASONS)
