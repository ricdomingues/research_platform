from collections import Counter
from dataclasses import replace

from sqlalchemy import select

from tests.integration.support import DAY, count, flat_raw, scenario_bars, submit_default
from tests.support import et
from virtual_orders.alerts.watchlist import add_ticker
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.storage import tables
from virtual_orders.worker import jobs as jobs_module
from virtual_orders.worker.jobs import JobResult, WorkerJobs


def run_kinds(engine):
    with engine.connect() as conn:
        return Counter(conn.execute(select(tables.evaluation_runs.c.kind)).scalars())


def outbox_keys(engine):
    with engine.connect() as conn:
        return list(conn.execute(select(tables.alert_outbox.c.alert_key).order_by(tables.alert_outbox.c.id)).scalars())


def watch_msft(worker):
    worker.bars.load(scenario_bars(ticker="MSFT"))
    with worker.services.engine.begin() as conn:
        add_ticker(conn, "MSFT", added_at=et(DAY, "09:00"))


def test_live_and_watchlist_jobs_outside_the_session_do_nothing(worker):
    jobs = WorkerJobs(worker.services)
    assert jobs.live_cycle() == JobResult("live_cycle", False, "OUTSIDE_SESSION")
    assert jobs.watchlist() == JobResult("watchlist", False, "OUTSIDE_SESSION")
    assert count(worker.services.engine, "evaluation_runs") == 0


def test_live_cycle_only_evaluates_orders(worker):
    engine = worker.services.engine
    submit_default(engine)
    worker.bars.load(scenario_bars())
    watch_msft(worker)
    worker.clock.set(et(DAY, "10:30"))

    assert WorkerJobs(worker.services).live_cycle() == JobResult("live_cycle", True, "COMPLETED")

    assert run_kinds(engine) == Counter({"LIVE": 1})
    assert outbox_keys(engine) == []  # enqueueing runs in deliver_alerts, opening and end_of_day (D24)
    assert all(call[0] != "MSFT" for call in worker.bars.calls)  # watchlist ingestion is its own job (D26)


def test_watchlist_job_ingests_and_records_feed_failures_on_its_own(worker):
    engine = worker.services.engine
    watch_msft(worker)
    worker.clock.set(et(DAY, "10:30"))
    jobs = WorkerJobs(worker.services)

    assert jobs.watchlist() == JobResult("watchlist", True, "COMPLETED")
    assert run_kinds(engine) == Counter({"WATCHLIST": 1})
    assert ("MSFT", et(DAY, "09:30"), et(DAY, "10:30")) in worker.bars.calls

    worker.bars.failing.add("MSFT")
    worker.clock.set(et(DAY, "10:32"))
    assert jobs.watchlist() == JobResult("watchlist", True, "COMPLETED_WITH_INGEST_FAILURES:1")


def test_watchlist_without_tickers_or_without_a_webhook(worker):
    engine = worker.services.engine
    worker.clock.set(et(DAY, "10:30"))
    assert WorkerJobs(worker.services).watchlist() == JobResult("watchlist", False, "NO_WATCHLIST")

    watch_msft(worker)
    disabled = WorkerJobs(replace(worker.services, alert_sink=None))
    assert disabled.watchlist() == JobResult("watchlist", True, "COMPLETED")
    assert run_kinds(engine) == Counter({"WATCHLIST": 1}) and outbox_keys(engine) == []


def test_a_failing_watchlist_never_touches_order_evaluation(worker, monkeypatch):
    def exploding(*args, **kwargs):
        raise RuntimeError("provider meltdown")

    monkeypatch.setattr(jobs_module, "run_watchlist_cycle", exploding)
    submit_default(worker.services.engine)
    worker.bars.load(scenario_bars())
    worker.clock.set(et(DAY, "10:30"))
    jobs = WorkerJobs(worker.services)

    assert jobs.runner("watchlist")() == JobResult("watchlist", False, "ERROR:RuntimeError")
    assert jobs.runner("live_cycle")() == JobResult("live_cycle", True, "COMPLETED")


def test_end_of_day_events_are_enqueued_without_any_live_cycle_job(worker):
    engine = worker.services.engine
    order_id = submit_default(engine, valid_sessions=1).auto_order_id
    worker.bars.load(flat_raw(DAY, "09:30", "16:00", 105))  # never fills: expires at the close
    worker.clock.set(et(DAY, "16:30"))

    assert WorkerJobs(worker.services).end_of_day() == JobResult("end_of_day", True, "COMPLETED")

    assert f"ORDER_EVENT:{order_id}:EXPIRED" in outbox_keys(engine)


def test_delivery_job_enqueues_events_written_outside_the_worker(worker):
    engine = worker.services.engine
    order_id = submit_default(engine).auto_order_id
    flag_order_review(engine, order_id, reason="MANUAL", ref="2025-11-25")  # e.g. through the API
    worker.clock.set(et(DAY, "08:00"))

    result = WorkerJobs(worker.services).deliver_alerts()

    assert result == JobResult("deliver_alerts", True, "DELIVERED:1 FAILED:0 EXPIRED:0")
    assert [key for key, _ in worker.sink.sent] == [f"ORDER_REVIEW:{order_id}:MANUAL:2025-11-25"]


def test_unexpected_errors_are_contained_by_the_runner(worker, monkeypatch):
    def exploding(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs_module, "run_live_cycle", exploding)
    worker.clock.set(et(DAY, "10:30"))
    assert WorkerJobs(worker.services).runner("live_cycle")() == JobResult("live_cycle", False, "ERROR:RuntimeError")


def test_opening_runs_only_before_the_open(worker):
    jobs = WorkerJobs(worker.services)
    worker.clock.set(et("2025-11-26", "09:25"))
    assert jobs.opening() == JobResult("opening", True, "COMPLETED")
    worker.clock.set(et("2025-11-26", "10:00"))
    assert jobs.opening() == JobResult("opening", False, "NO_SESSION_OPENING")
    worker.clock.set(et("2025-11-27", "09:25"))
    assert jobs.opening() == JobResult("opening", False, "NO_SESSION_OPENING")
    assert run_kinds(worker.services.engine) == Counter({"OPENING": 1})


def test_end_of_day_retries_when_quality_was_not_evaluated_then_settles(worker):
    engine = worker.services.engine
    order_id = submit_default(engine).auto_order_id
    worker.bars.load(scenario_bars())
    worker.bars.failing.add("AAPL")
    jobs = WorkerJobs(worker.services)

    worker.clock.set(et(DAY, "15:00"))
    assert jobs.end_of_day() == JobResult("end_of_day", False, "NO_CLOSED_SESSION_TODAY")
    worker.clock.set(et(DAY, "16:30"))
    assert jobs.end_of_day() == JobResult("end_of_day", True, "COMPLETED")  # D12: PROVIDER_FAILURE
    worker.bars.failing.clear()
    worker.clock.set(et(DAY, "18:30"))
    assert jobs.end_of_day() == JobResult("end_of_day", True, "COMPLETED")  # not settled yet: runs again
    assert jobs.end_of_day() == JobResult("end_of_day", False, "ALREADY_SETTLED")

    with engine.connect() as conn:
        quality = [e.prepared.event_key for e in stored_events(conn, order_id) if e.prepared.type == "DATA_QUALITY"]
    assert quality == ["DATA_QUALITY:2025-11-25"]
    assert len([key for key in outbox_keys(engine) if key.startswith("END_OF_DAY_SUMMARY:2025-11-25:")]) == 2


def test_health_watch_and_alert_delivery(worker):
    engine = worker.services.engine
    jobs = WorkerJobs(worker.services)
    worker.clock.set(et(DAY, "08:00"))
    assert jobs.health_watch() == JobResult("health_watch", True, "HEALTHY")

    order_id = submit_default(engine).auto_order_id
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=order_id))
    assert jobs.health_watch() == JobResult("health_watch", True, "DEGRADED")

    assert jobs.deliver_alerts() == JobResult("deliver_alerts", True, "DELIVERED:2 FAILED:0 EXPIRED:0")
    assert worker.sink.sent[0][0].startswith("HEALTH:")
    assert worker.sink.sent[1][0].startswith("INTEGRITY_INCIDENT:")  # enqueued by the delivery job itself
    disabled = WorkerJobs(replace(worker.services, alert_sink=None))
    assert disabled.deliver_alerts() == JobResult("deliver_alerts", False, "WEBHOOK_DISABLED")
