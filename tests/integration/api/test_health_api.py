import json
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from alembic import command
from fastapi.testclient import TestClient

from tests.integration.support import API_KEY, CODE_VERSION, DAY, TICKER, alembic_config, post_json, signal_body
from tests.support import et
from virtual_orders.api.app import create_app
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.ledger.runs import RunKind, RunStatus, finish_run, start_run
from virtual_orders.storage import tables
from virtual_orders.storage.database import make_engine


def health(api):
    response = api.client.get("/health")
    return response.status_code, response.json()


def test_fresh_system_is_healthy_before_and_at_the_open_and_stale_later_in_the_session(api):
    status, body = health(api)
    assert status == 200 and body["state"] == "HEALTHY" and body["causes"] == []
    assert body["facts"]["session_open_utc"] is None and body["facts"]["incidents_total"] == 0
    api.clock.set(et(DAY, "09:31"))
    status, body = health(api)
    assert status == 200 and body["state"] == "HEALTHY"
    assert body["facts"]["session_open_utc"] == et(DAY, "09:30").isoformat()
    api.clock.set(et(DAY, "10:30"))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    assert [c["code"] for c in body["causes"]] == ["LIVE_CYCLE_STALE"]


def test_three_failed_ingests_degrade_and_review_queue_is_informational(api):
    order_id = UUID(post_json(api.client, "/signals", signal_body()).json()["auto_order_id"])
    flag_order_review(api.services.engine, order_id, reason="MANUAL", ref="look")
    api.bars.failing.add(TICKER)
    for hm in ("10:00", "10:02", "10:04"):
        run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    causes = {c["code"]: c for c in body["causes"]}
    assert causes["INGEST_FAILURES_CONSECUTIVE"]["detail"] == {"feeds": {"fake_feed:AAPL": 3}}
    assert causes["NEEDS_REVIEW_QUEUE"]["severity"] == "INFO"
    assert causes["NEEDS_REVIEW_QUEUE"]["detail"] == {"count": 1, "reasons": {"MANUAL": 1}}


def test_integrity_incident_degrades_with_aggregated_groups(api):
    order_id = post_json(api.client, "/signals", signal_body()).json()["auto_order_id"]
    with api.services.engine.begin() as conn:
        for _ in range(2):
            record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=UUID(order_id)))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    incidents = next(c for c in body["causes"] if c["code"] == "INTEGRITY_INCIDENTS")
    assert incidents["severity"] == "DEGRADED" and incidents["detail"]["total"] == 2
    assert incidents["detail"]["groups"][0]["occurrences"] == 2
    assert incidents["detail"]["groups"][0]["affected_orders"] == [order_id]


def test_order_without_projection_is_degraded_and_counted_as_frozen(api):
    order_id = post_json(api.client, "/signals", signal_body()).json()["auto_order_id"]
    with api.services.engine.begin() as conn:
        conn.execute(tables.order_state.delete().where(tables.order_state.c.order_id == UUID(order_id)))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    causes = {c["code"]: c for c in body["causes"]}
    assert causes["PROJECTION_MISSING_ORDERS"]["detail"] == {"count": 1, "orders": [order_id]}
    assert causes["FROZEN_ORDERS"]["detail"] == {"count": 1, "frozen_projections": 0, "without_projection": 1}


def test_failed_cycle_details_never_expose_the_exception_message_in_the_response_body(api):
    message = 'OperationalError(\'connection to server at "db.internal" failed\')'
    with api.services.engine.begin() as conn:
        run = start_run(conn, RunKind.LIVE, et(DAY, "09:00"), CODE_VERSION)
        finish_run(conn, run.run_id, RunStatus.FAILED, {"error": message})
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    cause = next(c for c in body["causes"] if c["code"] == "LAST_CYCLE_FAILED")
    assert cause["detail"]["error"] == "OperationalError"
    raw_body = json.dumps(body)
    assert "db.internal" not in raw_body
    assert "OperationalError" in raw_body


def test_database_unavailable_is_503_without_facts(api):
    unreachable = make_engine("postgresql+psycopg://vo:vo@127.0.0.1:1/unreachable")
    services = replace(api.services, engine=unreachable)
    with TestClient(create_app(services), headers={"X-API-Key": API_KEY}) as client:
        response = client.get("/health")
    unreachable.dispose()
    assert response.status_code == 503
    assert response.json() == {
        "state": "UNHEALTHY",
        "causes": [{"code": "DATABASE_UNAVAILABLE", "severity": "UNHEALTHY", "detail": {"error": "OperationalError"}}],
        "facts": None,
    }


def test_schema_behind_the_migration_head_is_503_without_facts(api, database_url):
    command.downgrade(alembic_config(database_url), "0001")
    status, body = health(api)
    assert status == 503
    assert body == {
        "state": "UNHEALTHY",
        "causes": [{"code": "SCHEMA_NOT_AT_HEAD", "severity": "UNHEALTHY",
                    "detail": {"expected": "0005", "found": "0001"}}],
        "facts": None,
    }


def test_failed_live_run_staleness_uses_its_recorded_market_now(api):
    with api.services.engine.begin() as conn:
        run = start_run(conn, RunKind.LIVE, et(DAY, "09:00"), CODE_VERSION)
        finish_run(conn, run.run_id, RunStatus.FAILED,
                   {"error": "RuntimeError('x')", "market_now": et(DAY, "10:00")})
    api.clock.set(et(DAY, "10:30"))
    status, body = health(api)
    assert status == 200 and body["state"] == "DEGRADED"
    stale = next(c for c in body["causes"] if c["code"] == "LIVE_CYCLE_STALE")
    assert datetime.fromisoformat(stale["detail"]["last_market_now"]) == et(DAY, "10:00")
