from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import event, text

from tests.integration.support import CODE_VERSION, DAY, post_json, signal_body
from tests.support import et
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels import health as health_module
from virtual_orders.readmodels.health import HealthState, build_health_report, missing_job_runs
from virtual_orders.readmodels.quality import pending_quality_sessions
from virtual_orders.storage import tables

SESSION = date(2025, 11, 25)


def body(api):
    response = api.client.get("/health")
    assert response.status_code == 200
    return response.json()


def test_slow_health_query_is_degraded_not_database_unavailable(engine, monkeypatch):
    def slow(conn, *, now):
        conn.execute(text("SELECT pg_sleep(2)"))
        raise AssertionError("statement_timeout did not fire")

    monkeypatch.setattr(health_module, "collect_health_snapshot", slow)
    report = build_health_report(engine, now=et(DAY, "08:00"), eval_interval_minutes=2, statement_timeout_ms=50)
    assert report.state is HealthState.DEGRADED and report.snapshot is None
    assert [c.code for c in report.causes] == ["HEALTH_QUERY_TIMEOUT"]


@pytest.mark.parametrize("day, hm", [
    ("2025-11-27", "11:00"),  # Thanksgiving holiday
    ("2025-11-29", "11:00"),  # Saturday
    ("2025-11-25", "09:29"),  # before the open
    ("2025-11-25", "16:00"),  # exactly at the close
    ("2025-11-28", "13:00"),  # exactly at the half-day early close
])
def test_outside_a_session_there_is_no_session_open_and_no_staleness(api, day, hm):
    api.clock.set(et(day, hm))
    payload = body(api)
    assert payload["facts"]["session_open_utc"] is None
    assert "LIVE_CYCLE_STALE" not in {c["code"] for c in payload["causes"]}


def test_half_day_session_is_detected_until_its_early_close(api):
    api.clock.set(et("2025-11-28", "12:59"))
    payload = body(api)
    assert payload["facts"]["session_open_utc"] == et("2025-11-28", "09:30").isoformat()
    assert [c["code"] for c in payload["causes"]] == ["LIVE_CYCLE_STALE"]


def test_review_flag_without_reasons_is_counted_as_unspecified(api):
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    with api.services.engine.begin() as conn:
        conn.execute(tables.order_state.update().where(tables.order_state.c.order_id == order_id)
                     .values(needs_review=True))
    cause = next(c for c in body(api)["causes"] if c["code"] == "NEEDS_REVIEW_QUEUE")
    assert cause["detail"] == {"count": 1, "reasons": {"UNSPECIFIED": 1}}


def test_quality_not_evaluated_disappears_once_the_session_is_rechecked(api):
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    engine = api.services.engine
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        eod = start_run(conn, RunKind.END_OF_DAY, as_of, CODE_VERSION, detail={"session_day": SESSION})
        finish_run(conn, eod.run_id, RunStatus.COMPLETED,
                   {"session_day": SESSION, "not_evaluated": {str(order_id): "PROVIDER_FAILURE"}})

    cause = next(c for c in body(api)["causes"] if c["code"] == "QUALITY_NOT_EVALUATED")
    assert cause["detail"] == {"session_days": ["2025-11-25"], "count": 1, "reasons": {"PROVIDER_FAILURE": 1}}
    with engine.connect() as conn:
        pending = pending_quality_sessions(conn)
    assert [(p.order_id, p.session_day, p.source_run_id, p.reason, p.ticker, p.price_source) for p in pending] == [
        (order_id, SESSION, eod.run_id, "PROVIDER_FAILURE", "AAPL", "fake_feed")
    ]

    with engine.begin() as conn:
        recheck = start_run(conn, RunKind.QUALITY_RECHECK, as_of, CODE_VERSION)
        conn.execute(tables.data_quality_rechecks.insert().values(
            order_id=order_id, session_date=SESSION, recheck_key="DATA_QUALITY_RECHECK:test", run_id=recheck.run_id,
            source_run_id=eod.run_id, data_as_of=as_of, payload={},
        ))
    assert "QUALITY_NOT_EVALUATED" not in {c["code"] for c in body(api)["causes"]}
    with engine.connect() as conn:
        assert pending_quality_sessions(conn) == []


def job_run(engine, kind, day, status=RunStatus.COMPLETED):
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, kind, as_of, CODE_VERSION, detail={"session_day": day})
        finish_run(conn, run.run_id, status, {"session_day": day})


def cause_map(payload):
    return {c["code"]: c for c in payload["causes"]}


def test_missing_opening_and_end_of_day_runs_are_visible_after_their_grace(api):
    engine = api.services.engine
    job_run(engine, RunKind.OPENING, SESSION)  # D38 anchor: the first recorded session is never flagged
    job_run(engine, RunKind.END_OF_DAY, SESSION)
    next_day = date(2025, 11, 26)

    api.clock.set(et("2025-11-26", "09:59"))  # open + 29 min: still inside the opening grace
    assert "OPENING_MISSING" not in cause_map(body(api))

    api.clock.set(et("2025-11-26", "12:00"))
    causes = cause_map(body(api))
    assert causes["OPENING_MISSING"]["detail"] == {"session_days": ["2025-11-26"]}
    assert "END_OF_DAY_MISSING" not in causes

    api.clock.set(et("2025-11-26", "19:00"))  # close + 3 h: the 18:30 retry had its chance
    payload = body(api)
    assert cause_map(payload)["END_OF_DAY_MISSING"]["detail"] == {"session_days": ["2025-11-26"]}
    assert payload["facts"]["missing_runs"] == {"OPENING": ["2025-11-26"], "END_OF_DAY": ["2025-11-26"]}

    job_run(engine, RunKind.OPENING, next_day, status=RunStatus.FAILED)  # a failed run is still missing
    assert "OPENING_MISSING" in cause_map(body(api))

    job_run(engine, RunKind.OPENING, next_day)
    job_run(engine, RunKind.END_OF_DAY, next_day)
    payload = body(api)
    assert payload["state"] == "HEALTHY" and payload["causes"] == []
    assert payload["facts"]["missing_runs"] == {}


def test_half_day_end_of_day_deadline_follows_the_1830_retry_not_the_close(api):
    """D38 fix: close + 3h on a 13:00 half day is 16:00 ET, before the 18:30 retry even runs; the real
    deadline is the later of close + 3h and 19:00 ET (18:30 retry + 30 min)."""
    engine = api.services.engine
    for day in (SESSION, date(2025, 11, 26)):  # keep the surrounding sessions clean of their own MISSING causes
        job_run(engine, RunKind.OPENING, day)
        job_run(engine, RunKind.END_OF_DAY, day)

    api.clock.set(et("2025-11-28", "16:25"))
    assert "END_OF_DAY_MISSING" not in cause_map(body(api))

    api.clock.set(et("2025-11-28", "18:59"))
    assert "END_OF_DAY_MISSING" not in cause_map(body(api))

    api.clock.set(et("2025-11-28", "19:00"))
    assert cause_map(body(api))["END_OF_DAY_MISSING"]["detail"] == {"session_days": ["2025-11-28"]}


def test_without_any_opening_or_end_of_day_run_nothing_is_missing(api):
    api.clock.set(et("2025-12-03", "20:00"))
    payload = body(api)
    assert payload["facts"]["missing_runs"] == {}
    assert not {"OPENING_MISSING", "END_OF_DAY_MISSING"} & set(cause_map(payload))


def test_missing_runs_read_only_sessions_inside_the_checked_range(api):
    engine = api.services.engine
    job_run(engine, RunKind.OPENING, date(2025, 10, 1))  # anchor far outside the lookback
    job_run(engine, RunKind.END_OF_DAY, date(2025, 10, 1))
    seen: list = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        if "since_day" in statement:
            seen.append(parameters)

    event.listen(engine, "before_cursor_execute", spy)
    try:
        with engine.connect() as conn:
            missing = missing_job_runs(conn, now=et("2025-11-26", "19:00"))
    finally:
        event.remove(engine, "before_cursor_execute", spy)

    assert len(seen) == 1 and seen[0]["since_day"] >= date(2025, 11, 1)  # D50 (T9): bounded read
    assert "2025-11-26" in missing["END_OF_DAY"] and "2025-10-02" not in missing["OPENING"]
