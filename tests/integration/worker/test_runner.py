import logging
import signal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from tests.integration.support import CODE_VERSION, DAY
from tests.support import et
from virtual_orders.ledger.worker_sessions import host_fingerprint
from virtual_orders.storage import tables
from virtual_orders.storage.database import WORKER_LOCK_KEY
from virtual_orders.worker import runner as runner_module
from virtual_orders.worker.jobs import JobResult
from virtual_orders.worker.runner import acquire_worker_lock, release_worker_lock, run_worker, worker_lock_held


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[tuple] = []
        self.started = False
        self.stopped = False
        self.shutdown_calls: list[bool] = []  # each entry is the `wait` argument, in call order

    def add_job(self, func, trigger, *, id, name, max_instances, coalesce, misfire_grace_time):  # noqa: A002
        self.jobs.append((id, func, trigger, name, max_instances, coalesce, misfire_grace_time))

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = True) -> None:
        self.stopped = True
        self.shutdown_calls.append(wait)


def assert_lock_is_free(engine):
    lock = acquire_worker_lock(engine)
    assert lock is not None
    release_worker_lock(lock)


def test_run_worker_registers_every_job_then_releases_the_lock_and_closes(worker):
    scheduler = FakeScheduler()
    assert run_worker(worker.services, scheduler_factory=lambda: scheduler, install_signal_handlers=False) == 0
    assert [job[0] for job in scheduler.jobs] == ["live_cycle", "watchlist", "opening", "end_of_day", "health_watch",
                                                  "deliver_alerts", "worker_lock"]
    assert all(job[3] == job[0] and job[4:] == (1, True, 60) for job in scheduler.jobs)
    assert scheduler.started and worker.closed == [1]
    assert_lock_is_free(worker.services.engine)

    live = {job[0]: job[1] for job in scheduler.jobs}["live_cycle"]
    worker.clock.set(et(DAY, "08:00"))
    assert live() == JobResult("live_cycle", False, "OUTSIDE_SESSION")


def test_a_second_worker_exits_without_scheduling(worker):
    held = acquire_worker_lock(worker.services.engine)
    assert held is not None
    try:
        factory_calls: list[int] = []
        code = run_worker(worker.services, scheduler_factory=lambda: factory_calls.append(1) or FakeScheduler(),
                          install_signal_handlers=False)
        assert code == 2 and factory_calls == [] and worker.closed == [1]
    finally:
        release_worker_lock(held)


def test_services_are_closed_and_the_lock_released_when_the_scheduler_fails(worker):
    class Broken(FakeScheduler):
        def start(self) -> None:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_worker(worker.services, scheduler_factory=Broken, install_signal_handlers=False)
    assert worker.closed == [1]
    assert_lock_is_free(worker.services.engine)


def test_termination_signals_shut_the_scheduler_down_gracefully(worker, monkeypatch):
    installed: dict = {}
    monkeypatch.setattr(runner_module.signal, "signal", lambda signum, handler: installed.__setitem__(signum, handler))

    class SignalledWhileRunning(FakeScheduler):
        def start(self) -> None:
            installed[signal.SIGTERM](signal.SIGTERM, None)

    scheduler = SignalledWhileRunning()
    assert run_worker(worker.services, scheduler_factory=lambda: scheduler) == 0
    assert scheduler.stopped and set(installed) == {signal.SIGTERM, signal.SIGINT}
    assert scheduler.shutdown_calls == [True]  # the signal path always waits for running jobs
    assert worker.closed == [1]


def test_a_second_signal_does_not_shut_the_scheduler_down_twice(worker, monkeypatch):
    """T16: a real APScheduler raises SchedulerNotRunningError on a second shutdown; the stop handler must
    be idempotent so a double Ctrl+C (or a signal racing the lock-loss shutdown) never unwinds start()."""
    installed: dict = {}
    monkeypatch.setattr(runner_module.signal, "signal", lambda signum, handler: installed.__setitem__(signum, handler))

    class SignalledTwice(FakeScheduler):
        def start(self) -> None:
            installed[signal.SIGTERM](signal.SIGTERM, None)
            installed[signal.SIGINT](signal.SIGINT, None)  # e.g. a second Ctrl+C

    scheduler = SignalledTwice()
    assert run_worker(worker.services, scheduler_factory=lambda: scheduler) == 0
    assert scheduler.shutdown_calls == [True]
    assert worker.closed == [1]


def test_a_signal_after_the_lock_is_lost_does_not_shut_down_twice(worker, monkeypatch):
    installed: dict = {}
    monkeypatch.setattr(runner_module.signal, "signal", lambda signum, handler: installed.__setitem__(signum, handler))
    engine = worker.services.engine

    class LosesTheLockThenSignalled(FakeScheduler):
        def start(self) -> None:
            watch = {job[0]: job[1] for job in self.jobs}["worker_lock"]
            assert watch() == JobResult("worker_lock", True, "HELD")
            with engine.begin() as conn:  # e.g. a database restart ended the session holding the lock
                conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND objid = CAST(:key AS oid) AND pid <> pg_backend_pid()"
                ), {"key": WORKER_LOCK_KEY})
            assert watch() == JobResult("worker_lock", False, "LOCK_LOST")
            installed[signal.SIGTERM](signal.SIGTERM, None)  # a signal arriving after the lock is already gone

    scheduler = LosesTheLockThenSignalled()
    assert run_worker(worker.services, scheduler_factory=lambda: scheduler, install_signal_handlers=True) == 3
    assert scheduler.shutdown_calls == [False]  # only the lock-loss shutdown ran; the signal was a no-op
    assert worker.closed == [1]
    assert_lock_is_free(engine)


def test_a_lost_worker_lock_alerts_and_stops_the_scheduler(worker):
    engine = worker.services.engine

    class LosesTheLock(FakeScheduler):
        def start(self) -> None:
            watch = {job[0]: job[1] for job in self.jobs}["worker_lock"]
            assert watch() == JobResult("worker_lock", True, "HELD")
            with engine.begin() as conn:  # e.g. a database restart ended the session holding the lock
                conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND objid = CAST(:key AS oid) AND pid <> pg_backend_pid()"
                ), {"key": WORKER_LOCK_KEY})
            assert watch() == JobResult("worker_lock", False, "LOCK_LOST")

    scheduler = LosesTheLock()
    assert run_worker(worker.services, scheduler_factory=lambda: scheduler, install_signal_handlers=False) == 3
    assert scheduler.stopped and worker.closed == [1]
    ((key, document),) = worker.sink.sent
    assert key.startswith("WORKER_LOCK_LOST:") and document["kind"] == "WORKER_LOCK_LOST"
    assert_lock_is_free(engine)


def test_a_live_connection_whose_lock_was_released_counts_as_lost(worker):
    lock = acquire_worker_lock(worker.services.engine)
    assert lock is not None
    try:
        assert worker_lock_held(lock) is True
        lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK_KEY})
        lock.commit()
        assert worker_lock_held(lock) is False  # the session is alive, but the lock is gone (T16)
    finally:
        lock.close()


def session_rows(engine):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(
            select(tables.worker_sessions).order_by(tables.worker_sessions.c.id)).mappings()]


def test_host_fingerprint_is_a_short_hash_and_never_the_name():
    fingerprint = host_fingerprint("worker-host")
    assert fingerprint is not None and len(fingerprint) == 16 and "worker" not in fingerprint
    assert fingerprint == host_fingerprint("worker-host") != host_fingerprint("other-host")
    assert host_fingerprint(None) is None and host_fingerprint("") is None


def test_a_session_records_its_start_and_its_signalled_stop(worker, monkeypatch):
    installed: dict = {}
    monkeypatch.setattr(runner_module.signal, "signal", lambda signum, handler: installed.__setitem__(signum, handler))

    class SignalledWhileRunning(FakeScheduler):
        def start(self) -> None:
            installed[signal.SIGTERM](signal.SIGTERM, None)

    assert run_worker(worker.services, scheduler_factory=SignalledWhileRunning, hostname=lambda: "worker-host") == 0
    start, stop = session_rows(worker.services.engine)
    assert (start["event"], start["code_version"], start["host_fingerprint"]) == (
        "STARTED", CODE_VERSION, host_fingerprint("worker-host"))
    assert (stop["session_id"], stop["event"], stop["exit_code"], stop["reason"]) == (
        start["session_id"], "STOPPED", 0, "SIGNAL")
    assert start["recorded_at"] <= stop["recorded_at"]
    assert worker.closed == [1]


def test_a_scheduler_that_returns_on_its_own_is_recorded_as_stopped(worker):
    assert run_worker(worker.services, scheduler_factory=FakeScheduler, install_signal_handlers=False,
                      hostname=lambda: None) == 0
    start, stop = session_rows(worker.services.engine)
    assert start["host_fingerprint"] is None
    assert (stop["exit_code"], stop["reason"]) == (0, "SCHEDULER_STOPPED")


def test_a_lost_lock_is_recorded_with_exit_code_3(worker):
    engine = worker.services.engine

    class LosesTheLock(FakeScheduler):
        def start(self) -> None:
            watch = {job[0]: job[1] for job in self.jobs}["worker_lock"]
            with engine.begin() as conn:
                conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND objid = CAST(:key AS oid) AND pid <> pg_backend_pid()"
                ), {"key": WORKER_LOCK_KEY})
            assert watch() == JobResult("worker_lock", False, "LOCK_LOST")

    assert run_worker(worker.services, scheduler_factory=LosesTheLock, install_signal_handlers=False) == 3
    _, stop = session_rows(engine)
    assert (stop["exit_code"], stop["reason"]) == (3, "LOCK_LOST")
    assert_lock_is_free(engine)


def test_a_failing_scheduler_is_recorded_and_still_raises(worker):
    class Broken(FakeScheduler):
        def start(self) -> None:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_worker(worker.services, scheduler_factory=Broken, install_signal_handlers=False)
    _, stop = session_rows(worker.services.engine)
    assert (stop["exit_code"], stop["reason"]) == (1, "UNCAUGHT_EXCEPTION")
    assert worker.closed == [1]


def test_a_worker_that_never_gets_the_lock_is_not_a_session(worker):
    held = acquire_worker_lock(worker.services.engine)
    assert held is not None
    try:
        assert run_worker(worker.services, scheduler_factory=FakeScheduler, install_signal_handlers=False) == 2
    finally:
        release_worker_lock(held)
    assert session_rows(worker.services.engine) == []


def test_a_failing_session_write_never_stops_the_worker_and_logs_only_the_type(worker, monkeypatch, caplog):
    def failing(*args, **kwargs):
        raise SQLAlchemyError("postgresql+psycopg://vo:hunter2@db/vo")

    monkeypatch.setattr(runner_module, "record_worker_start", failing)
    scheduler = FakeScheduler()
    with caplog.at_level(logging.WARNING, logger="virtual_orders.worker"):
        code = run_worker(worker.services, scheduler_factory=lambda: scheduler, install_signal_handlers=False)
    assert code == 0 and scheduler.started and worker.closed == [1]
    assert "worker session start not recorded: SQLAlchemyError" in caplog.text
    assert "hunter2" not in caplog.text
    assert session_rows(worker.services.engine) == []  # no stop row without a recorded start
    assert_lock_is_free(worker.services.engine)


def test_a_failing_stop_write_still_releases_the_lock_and_closes(worker, monkeypatch, caplog):
    def failing(*args, **kwargs):
        raise SQLAlchemyError("down")

    monkeypatch.setattr(runner_module, "record_worker_stop", failing)
    with caplog.at_level(logging.WARNING, logger="virtual_orders.worker"):
        assert run_worker(worker.services, scheduler_factory=FakeScheduler, install_signal_handlers=False) == 0
    assert "worker session stop not recorded: SQLAlchemyError" in caplog.text
    assert [row["event"] for row in session_rows(worker.services.engine)] == ["STARTED"]
    assert worker.closed == [1]
    assert_lock_is_free(worker.services.engine)
