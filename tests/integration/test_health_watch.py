from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from tests.integration.alert_support import RecordingSink
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    FakeBarSource,
    FakeReference,
    count,
    feeds,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.alerts import health_watch as health_watch_module
from virtual_orders.alerts.health_watch import HealthSignature, HealthWatcher, should_alert
from virtual_orders.alerts.summary import enqueue_end_of_day_summary
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.rebuild import rebuild_projection
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.orders import delete_projection
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.readmodels.health import HealthState, database_unavailable
from virtual_orders.storage import tables
from virtual_orders.storage.database import make_engine

NOW = et(DAY, "08:00")  # outside the session: no staleness noise
SESSION = date(2025, 11, 25)


def outbox(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.alert_outbox).order_by(tables.alert_outbox.c.id)).all()


def log(engine):
    with engine.connect() as conn:
        return conn.execute(select(tables.health_state_log).order_by(tables.health_state_log.c.id)).all()


def incident(engine, order_id):
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=order_id))


def test_should_alert_rules():
    healthy = HealthSignature(HealthState.HEALTHY, ())
    degraded = HealthSignature(HealthState.DEGRADED, ("INTEGRITY_INCIDENTS",))
    wider = HealthSignature(HealthState.DEGRADED, ("FROZEN_ORDERS", "INTEGRITY_INCIDENTS"))
    narrower = HealthSignature(HealthState.DEGRADED, ("FROZEN_ORDERS",))
    unhealthy = HealthSignature(HealthState.UNHEALTHY, ("DATABASE_UNAVAILABLE",))
    assert should_alert(None, degraded) and should_alert(healthy, degraded) and should_alert(degraded, wider)
    assert should_alert(degraded, healthy) and should_alert(unhealthy, healthy)  # D32: recovery
    assert not should_alert(None, healthy) and not should_alert(healthy, healthy)
    assert not should_alert(degraded, degraded) and not should_alert(wider, narrower)


def test_transitions_are_logged_and_alerted_once_even_after_a_restart(engine):
    sink = RecordingSink()
    watcher = HealthWatcher(engine, eval_interval_minutes=2)

    first = watcher.observe(NOW, sink)
    assert (first.state, first.transitioned, first.alerted) == (HealthState.HEALTHY, True, False)
    assert watcher.observe(NOW, sink).transitioned is False

    order_id = submit_default(engine).auto_order_id
    incident(engine, order_id)
    degraded = watcher.observe(NOW, sink)
    assert (degraded.state, degraded.transitioned, degraded.alerted) == (HealthState.DEGRADED, True, True)
    assert watcher.observe(NOW, sink).alerted is False
    assert HealthWatcher(engine, eval_interval_minutes=2).observe(NOW, sink).alerted is False  # restart

    with engine.begin() as conn:
        delete_projection(conn, order_id)  # a new DEGRADED cause while already degraded
    assert watcher.observe(NOW, sink).alerted is True

    rows = log(engine)
    assert [row.state for row in rows] == ["HEALTHY", "DEGRADED", "DEGRADED"]
    alerts = outbox(engine)
    assert [row.alert_key for row in alerts] == [f"HEALTH:{rows[1].id}", f"HEALTH:{rows[2].id}"]
    document = alerts[1].document
    assert document["state"] == "DEGRADED" and document["previous_state"] == "DEGRADED"
    assert {cause["code"] for cause in document["causes"]} >= {"INTEGRITY_INCIDENTS", "PROJECTION_MISSING_ORDERS"}
    assert all(set(cause) == {"code", "severity"} for cause in document["causes"])
    assert sink.sent == []  # persisted alerts go through the outbox, never straight to the sink


def test_disabled_webhook_still_logs_transitions_without_enqueueing(engine):
    watcher = HealthWatcher(engine, eval_interval_minutes=2)
    incident(engine, submit_default(engine).auto_order_id)
    observation = watcher.observe(NOW, None)
    assert observation.transitioned is True and observation.alerted is False
    assert count(engine, "health_state_log") == 1 and count(engine, "alert_outbox") == 0


def test_return_to_healthy_sends_one_recovery_alert(engine):
    sink = RecordingSink()
    watcher = HealthWatcher(engine, eval_interval_minutes=2)
    order_id = submit_default(engine).auto_order_id
    with engine.begin() as conn:
        delete_projection(conn, order_id)
    assert watcher.observe(NOW, sink).alerted is True

    rebuild_projection(engine, order_id)  # the missing projection is restored: every cause clears
    recovered = watcher.observe(NOW, sink)

    assert (recovered.state, recovered.transitioned, recovered.alerted) == (HealthState.HEALTHY, True, True)
    assert watcher.observe(NOW, sink).alerted is False
    rows = log(engine)
    assert [row.state for row in rows] == ["DEGRADED", "HEALTHY"]
    alerts = outbox(engine)
    assert [row.alert_key for row in alerts] == [f"HEALTH:{rows[0].id}", f"HEALTH:{rows[1].id}"]
    assert alerts[0].document["recovered"] is False
    assert (alerts[1].document["state"], alerts[1].document["previous_state"], alerts[1].document["recovered"]) == (
        "HEALTHY", "DEGRADED", True,
    )


def test_recovery_after_a_database_outage_is_logged_and_alerted_through_the_outbox(engine, monkeypatch):
    sink = RecordingSink()
    watcher = HealthWatcher(engine, eval_interval_minutes=2)
    outage = OperationalError("SELECT 1", {}, Exception("down"))

    def unreachable(*args, **kwargs):
        return database_unavailable(outage)

    def log_unavailable(conn):
        raise outage

    with monkeypatch.context() as patch:
        patch.setattr(health_watch_module, "build_health_report", unreachable)
        patch.setattr(health_watch_module, "_latest", log_unavailable)
        assert watcher.observe(NOW, sink).alerted is True  # direct, in memory

    recovered = watcher.observe(NOW, sink)

    assert (recovered.state, recovered.transitioned, recovered.alerted) == (HealthState.HEALTHY, True, True)
    ((direct_key, _),) = sink.sent
    assert direct_key.startswith("HEALTH_DIRECT:UNHEALTHY:")
    (row,) = outbox(engine)
    assert (row.document["state"], row.document["previous_state"], row.document["recovered"]) == (
        "HEALTHY", "UNHEALTHY", True,
    )
    assert [entry.state for entry in log(engine)] == ["HEALTHY"]


def test_unreachable_database_alerts_directly_once_without_leaking_the_host():
    unreachable = make_engine("postgresql+psycopg://vo:vo@127.0.0.1:1/unreachable")
    sink = RecordingSink()
    watcher = HealthWatcher(unreachable, eval_interval_minutes=2)
    try:
        first = watcher.observe(NOW, sink)
        second = watcher.observe(NOW, sink)
    finally:
        unreachable.dispose()
    assert (first.state, first.alerted, second.alerted) == (HealthState.UNHEALTHY, True, False)
    ((key, document),) = sink.sent
    assert key.startswith("HEALTH_DIRECT:UNHEALTHY:")
    assert document["causes"] == [{"code": "DATABASE_UNAVAILABLE", "severity": "UNHEALTHY"}]
    assert "127.0.0.1" not in str(document)
    # I2/T12: the D24 envelope reaches the direct DB-down alert too, not just outbox-backed ones.
    assert document["kind"] == "HEALTH" and document["alert_key"] == key and document["schema_version"] == 1


def test_end_of_day_summary_is_enqueued_once_per_cycle_run(engine):
    order_id = submit_default(engine, valid_sessions=1).auto_order_id
    report = run_end_of_day(engine, gateway=feeds(FakeBarSource(scenario_bars())), reference=FakeReference(),
                            session_day=SESSION, code_version=CODE_VERSION, market_now=et(DAY, "16:30"))

    assert enqueue_end_of_day_summary(engine, report, session_day=SESSION, health_state=HealthState.HEALTHY,
                                      recheck=None) is True
    assert enqueue_end_of_day_summary(engine, report, session_day=SESSION, health_state=HealthState.HEALTHY,
                                      recheck=None) is False

    (row,) = outbox(engine)
    assert row.alert_key == f"END_OF_DAY_SUMMARY:{SESSION.isoformat()}:{report.cycle.run_id}"
    document = row.document
    assert document["session_day"] == "2025-11-25" and document["health_state"] == "HEALTHY"
    assert document["cycle_orders"] == 1 and document["quality_orders"] == 1
    assert document["events"]["TARGET2_HIT"] == 1 and document["events"]["DATA_QUALITY"] == 1
    assert document["not_evaluated"] == 0 and document["ingest_failures"] == []
    assert document["rechecked"] == 0 and document["recheck_terminal"] == 0 and document["undeliverable_alerts"] == 0
    assert UUID(document["quality_run_id"]) == report.quality.run_id
    assert order_id is not None
