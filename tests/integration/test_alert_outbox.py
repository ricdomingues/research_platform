import itertools
import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text

from tests.integration.alert_support import RecordingSink
from tests.integration.support import CODE_VERSION, DAY, FakeBarSource, count, feeds, scenario_bars, submit_default
from tests.support import et
from virtual_orders.alerts import outbox
from virtual_orders.alerts.outbox import (
    AlertKind,
    DeliveryReport,
    alert_envelope,
    deliver_pending_alerts,
    enqueue_alert,
    enqueue_event_alerts,
    enqueue_incident_alerts,
    enqueue_order_event_alerts,
    order_event_alerts_behind,
    undeliverable_alerts,
)
from virtual_orders.alerts.sink import AlertDeliveryFailed
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.replay import reproduce_orders
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.notify.n8n import N8nWebhook
from virtual_orders.readmodels.health import HealthState, Severity, build_health_report
from virtual_orders.storage import tables


def closed_order(engine):
    order_id = submit_default(engine).auto_order_id
    source = FakeBarSource(scenario_bars())
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id


def outbox_rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.alert_outbox).order_by(tables.alert_outbox.c.id)).all()


def attempts(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.alert_delivery_attempts)
                            .order_by(tables.alert_delivery_attempts.c.id)).all()


def db_now(engine) -> datetime:
    with engine.connect() as conn:
        return conn.execute(text("SELECT clock_timestamp()")).scalar_one()


def order_keys(order_id):
    return [f"ORDER_EVENT:{order_id}:{key}" for key in ("FILLED", "TARGET1_HIT", "TARGET2_HIT")]


def health_at_8(engine):
    return build_health_report(engine, now=et(DAY, "08:00"), eval_interval_minutes=2)  # outside the session


def test_order_events_are_enqueued_once_reviews_collapse_per_session_and_replays_are_ignored(engine):
    order_id = closed_order(engine)
    flag_order_review(engine, order_id, reason="MANUAL", ref="2025-11-25")
    for hm in ("10:10", "10:11"):
        flag_order_review(engine, order_id, reason="MISSING_BAR_UNVERIFIABLE", ref=et(DAY, hm).isoformat())
    reproduce_orders(engine, code_version=CODE_VERSION, order_ids=[order_id])

    assert enqueue_order_event_alerts(engine) == 5
    assert enqueue_order_event_alerts(engine) == 0

    rows = outbox_rows(engine)
    assert [row.alert_key for row in rows] == order_keys(order_id) + [
        f"ORDER_REVIEW:{order_id}:MANUAL:2025-11-25",
        f"ORDER_REVIEW:{order_id}:MISSING_BAR_UNVERIFIABLE:2025-11-25",  # two flags, one alert (D33)
    ]
    document = rows[0].document
    assert document["schema_version"] == 1 and document["kind"] == "ORDER_EVENT"
    assert document["alert_key"] == rows[0].alert_key and document["order_id"] == str(order_id)
    assert (document["ticker"], document["strategy"], document["direction"]) == ("AAPL", "REXSHARE", "LONG")
    assert document["event"]["type"] == "FILLED" and document["event"]["price"] == "101"
    assert rows[0].subject == str(order_id)
    review = rows[4].document
    assert review["kind"] == "ORDER_REVIEW" and review["review"] == {
        "reason": "MISSING_BAR_UNVERIFIABLE", "session_day": "2025-11-25", "first_ref": et(DAY, "10:10").isoformat(),
    }
    assert count(engine, "alert_event_marks") == 6  # every scanned live event is marked, collapsed ones included


def test_events_older_than_the_lookback_are_not_alerted_and_surface_as_behind(engine, monkeypatch):
    order_id = closed_order(engine)
    assert enqueue_order_event_alerts(engine) == 3  # alerts are enabled: marks exist from here on
    flag_order_review(engine, order_id, reason="MANUAL", ref="2025-11-25")
    monkeypatch.setattr(outbox, "ORDER_EVENT_LOOKBACK", timedelta(0))

    assert enqueue_order_event_alerts(engine) == 0
    with engine.connect() as conn:
        assert order_event_alerts_behind(conn) == 1
    report = health_at_8(engine)
    behind = next(c for c in report.causes if c.code == "ORDER_EVENT_ALERTS_BEHIND")
    assert behind.severity is Severity.INFO and behind.detail == {"count": 1}
    assert report.state is HealthState.HEALTHY


def test_behind_only_counts_events_inside_the_behind_window(engine, monkeypatch):
    order_id = closed_order(engine)
    assert enqueue_order_event_alerts(engine) == 3  # alerting started: marks exist from here on
    flag_order_review(engine, order_id, reason="MANUAL", ref="2025-11-25")
    monkeypatch.setattr(outbox, "ORDER_EVENT_LOOKBACK", timedelta(0))
    with engine.connect() as conn:
        assert order_event_alerts_behind(conn) == 1

    monkeypatch.setattr(outbox, "ORDER_EVENT_BEHIND_WINDOW", timedelta(0))  # D50 (M1): older than the cap
    with engine.connect() as conn:
        assert order_event_alerts_behind(conn) == 0


def test_without_any_mark_nothing_is_behind(engine, monkeypatch):
    closed_order(engine)  # webhook never enabled: no scan, no marks
    monkeypatch.setattr(outbox, "ORDER_EVENT_LOOKBACK", timedelta(0))
    with engine.connect() as conn:
        assert order_event_alerts_behind(conn) == 0
    assert "ORDER_EVENT_ALERTS_BEHIND" not in {c.code for c in health_at_8(engine).causes}


def test_integrity_incidents_are_alerted_once_each_without_their_detail(engine):
    order_id = submit_default(engine).auto_order_id
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone secret-detail", order_id=order_id))
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "orderless secret-detail"))

    assert enqueue_incident_alerts(engine) == 2
    assert enqueue_incident_alerts(engine) == 0

    with engine.connect() as conn:
        ids = list(conn.execute(select(tables.integrity_incidents.c.id).order_by(tables.integrity_incidents.c.id))
                   .scalars())
    rows = outbox_rows(engine)
    assert [row.alert_key for row in rows] == [f"INTEGRITY_INCIDENT:{incident_id}" for incident_id in ids]
    first, orderless = rows[0].document, rows[1].document
    assert first["kind"] == "INTEGRITY_INCIDENT" and first["incident_kind"] == errors.PROJECTION_MISSING
    assert first["order_id"] == str(order_id) and orderless["order_id"] is None
    assert "secret-detail" not in json.dumps([row.document for row in rows])


def test_enqueue_event_alerts_covers_orders_and_incidents(engine):
    order_id = closed_order(engine)
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=order_id))
    assert enqueue_event_alerts(engine) == 4
    assert enqueue_event_alerts(engine) == 0


def test_alert_envelope_merges_reserved_keys_last_so_a_caller_cannot_override_them():
    document = alert_envelope(alert_key="REAL_KEY", kind="REAL_KIND", document={
        "schema_version": 999, "alert_key": "SPOOFED", "kind": "SPOOFED", "reason": "ok",
    })
    assert document == {"reason": "ok", "schema_version": 1, "alert_key": "REAL_KEY", "kind": "REAL_KIND"}


def test_enqueue_alert_ignores_a_callers_attempt_to_override_the_envelope(engine):
    with engine.begin() as conn:
        created = enqueue_alert(conn, alert_key="TEST:1", kind=AlertKind.HEALTH, document={
            "schema_version": 999, "alert_key": "SPOOFED", "kind": "SPOOFED", "state": "DEGRADED",
        })
    assert created
    (row,) = outbox_rows(engine)
    assert row.document["schema_version"] == 1
    assert row.document["alert_key"] == "TEST:1"
    assert row.document["kind"] == AlertKind.HEALTH.value
    assert row.document["state"] == "DEGRADED"


def test_pending_alerts_are_delivered_once_in_order_even_after_a_restart(engine):
    order_id = closed_order(engine)
    enqueue_order_event_alerts(engine)
    sink = RecordingSink()

    assert deliver_pending_alerts(engine, sink) == DeliveryReport(delivered=3, failed=0)
    assert [key for key, _ in sink.sent] == [
        f"ORDER_EVENT:{order_id}:FILLED", f"ORDER_EVENT:{order_id}:TARGET1_HIT",
        f"ORDER_EVENT:{order_id}:TARGET2_HIT",
    ]
    restarted = RecordingSink()
    assert deliver_pending_alerts(engine, restarted) == DeliveryReport(delivered=0, failed=0)
    assert restarted.sent == []
    assert [row.outcome for row in attempts(engine)] == ["DELIVERED"] * 3


def test_deliver_pending_alerts_rejects_a_naive_now(engine):
    closed_order(engine)
    enqueue_order_event_alerts(engine)
    naive = datetime(2025, 11, 25, 12, 0)  # noqa: DTZ001 - deliberately naive, to be rejected
    with pytest.raises(ValueError, match="timezone-aware"):
        deliver_pending_alerts(engine, RecordingSink(), now=naive)


def test_a_failing_alert_backs_off_without_blocking_the_others(engine):
    order_id = closed_order(engine)
    enqueue_order_event_alerts(engine)
    filled, *others = order_keys(order_id)
    sink = RecordingSink()
    sink.failure = AlertDeliveryFailed(status_code=502, error_type="HTTPStatus")
    sink.failing_keys = {filled}
    start = db_now(engine)

    assert deliver_pending_alerts(engine, sink, now=start) == DeliveryReport(delivered=2, failed=1)
    assert [key for key, _ in sink.sent] == [filled, *others]  # the failure did not stop the run

    # Failure n waits backoff(n) = 1, 2, 4 minutes after the previous failed attempt.
    for minutes, expected in ((0.5, (0, 0)), (1, (0, 1)), (2.5, (0, 0)), (3, (0, 1)), (6.5, (0, 0))):
        report = deliver_pending_alerts(engine, sink, now=start + timedelta(minutes=minutes))
        assert report == DeliveryReport(*expected), minutes
    sink.failure = None
    assert deliver_pending_alerts(engine, sink, now=start + timedelta(minutes=7)) == DeliveryReport(1, 0)

    first = [a for a in attempts(engine) if a.alert_id == outbox_rows(engine)[0].id]
    assert [(a.outcome, a.status_code, a.error_type) for a in first] == [("FAILED", 502, "HTTPStatus")] * 3 + [
        ("DELIVERED", None, None)
    ]
    with engine.connect() as conn:
        assert undeliverable_alerts(conn) == 0


def test_alerts_older_than_a_day_are_expired_not_deleted_and_surface_as_info(engine):
    closed_order(engine)
    enqueue_order_event_alerts(engine)
    sink = RecordingSink()
    later = db_now(engine) + timedelta(hours=25)

    assert deliver_pending_alerts(engine, sink, now=later) == DeliveryReport(delivered=0, failed=0, expired=3)
    assert sink.sent == [] and len(outbox_rows(engine)) == 3
    assert [a.outcome for a in attempts(engine)] == ["EXPIRED"] * 3
    assert deliver_pending_alerts(engine, sink, now=later) == DeliveryReport(0, 0)  # expired once, never retried
    with engine.connect() as conn:
        assert undeliverable_alerts(conn) == 3
    report = health_at_8(engine)
    cause = next(c for c in report.causes if c.code == "UNDELIVERABLE_ALERTS")
    assert cause.severity is Severity.INFO and cause.detail == {"count": 3}
    assert report.state is HealthState.HEALTHY  # n8n is never in the critical path


def test_the_time_budget_leaves_the_rest_for_the_next_run(engine):
    order_id = closed_order(engine)
    enqueue_order_event_alerts(engine)
    ticks = itertools.count(0, 15)  # seconds: 0 at the start, 15 (< 20) before the 1st attempt, 30 before the 2nd
    sink = RecordingSink()

    report = deliver_pending_alerts(engine, sink, monotonic=lambda: float(next(ticks)))

    assert report == DeliveryReport(delivered=1, failed=0, budget_exhausted=True)
    assert [key for key, _ in sink.sent] == order_keys(order_id)[:1]
    assert deliver_pending_alerts(engine, RecordingSink()) == DeliveryReport(delivered=2, failed=0)


def test_unexpected_sink_errors_are_recorded_by_type_only(engine):
    closed_order(engine)
    enqueue_order_event_alerts(engine)
    sink = RecordingSink()
    sink.failure = RuntimeError("secret detail")
    assert deliver_pending_alerts(engine, sink) == DeliveryReport(delivered=0, failed=3)
    assert {(a.outcome, a.status_code, a.error_type) for a in attempts(engine)} == {("FAILED", None, "RuntimeError")}


def test_real_webhook_adapter_end_to_end_without_network(engine):
    requests: list[httpx.Request] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(204)))
    closed_order(engine)
    enqueue_order_event_alerts(engine)

    assert deliver_pending_alerts(engine, N8nWebhook(client, "https://n8n.test/hook")) == DeliveryReport(3, 0)

    body = json.loads(requests[0].content)
    assert body["alert_key"] == requests[0].headers["Idempotency-Key"]
    assert body["event"]["type"] == "FILLED" and body["event"]["price"] == "101"
    client.close()
