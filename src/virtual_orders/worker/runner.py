"""Worker process lifecycle (D20, D39): watched process lock, job registration, graceful shutdown, services closed."""

from __future__ import annotations

import logging
import signal
from collections.abc import Callable
from types import FrameType
from typing import Any, Protocol, cast

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from virtual_orders.alerts.outbox import alert_envelope
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.services import Services
from virtual_orders.storage.database import WORKER_LOCK_KEY
from virtual_orders.worker.jobs import JobResult, WorkerJobs
from virtual_orders.worker.schedule import (
    MARKET_TZ,
    MISFIRE_GRACE_SECONDS,
    WORKER_LOCK,
    build_schedule,
    worker_lock_schedule,
)

logger = logging.getLogger("virtual_orders.worker")
EXIT_OK = 0
EXIT_LOCKED = 2
EXIT_LOCK_LOST = 3

# pg_try_advisory_lock(bigint) stores the high 32 bits in classid, the low 32 bits in objid and objsubid = 1.
_LOCK_HELD = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM pg_locks
        WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted
          AND classid = CAST(0 AS oid) AND objid = CAST(:key AS oid) AND objsubid = 1
    )
    """
)


class Scheduler(Protocol):
    def add_job(
        self, func: Callable[[], object], trigger: Any, *, id: str, name: str, max_instances: int, coalesce: bool,
        misfire_grace_time: int,
    ) -> object: ...

    def start(self) -> None: ...

    def shutdown(self, wait: bool = True) -> None: ...


def blocking_scheduler() -> Scheduler:
    return cast(Scheduler, BlockingScheduler(timezone=MARKET_TZ))


def acquire_worker_lock(engine: Engine) -> Connection | None:
    conn = engine.connect()
    try:
        acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK_KEY}).scalar_one())
        conn.commit()
    except Exception:
        conn.close()
        raise
    if not acquired:
        conn.close()
        return None
    return conn


def release_worker_lock(conn: Connection) -> None:
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK_KEY})
        conn.commit()
    finally:
        conn.close()


def worker_lock_held(conn: Connection) -> bool:
    """D39: whether this session still holds WORKER_LOCK_KEY. A dropped or unusable connection counts as lost."""
    try:
        held = bool(conn.execute(_LOCK_HELD, {"key": WORKER_LOCK_KEY}).scalar_one())
        conn.commit()
    except SQLAlchemyError:
        return False
    return held


def _alert_lock_lost(services: Services) -> None:
    """Straight to the sink: the database that dropped the lock may be the thing that is down."""
    sink = services.alert_sink
    if sink is None:
        return
    observed = require_aware(services.clock(), "clock")
    alert_key = f"WORKER_LOCK_LOST:{observed.isoformat()}"
    try:
        sink.deliver(alert_key, alert_envelope(
            alert_key=alert_key, kind="WORKER_LOCK_LOST", document={"observed_at": observed.isoformat()}
        ))
    except Exception as exc:  # noqa: BLE001 - n8n is never in the critical path
        logger.warning("worker lock alert failed via %s: %s", sink.name, type(exc).__name__)


def run_worker(
    services: Services,
    *,
    scheduler_factory: Callable[[], Scheduler] = blocking_scheduler,
    install_signal_handlers: bool = True,
) -> int:
    lock: Connection | None = None
    lost = False
    stopping = False  # T16: a second SIGTERM/SIGINT, or a signal after lock loss, must never shut down twice
    try:
        lock = acquire_worker_lock(services.engine)
        if lock is None:
            logger.error("another worker holds the worker lock; exiting")
            return EXIT_LOCKED
        held: Connection = lock
        jobs = WorkerJobs(services)
        schedule = build_schedule(services.eval_interval_minutes)
        scheduler = scheduler_factory()

        def watch_lock() -> JobResult:
            nonlocal lost, stopping
            if worker_lock_held(held):
                return JobResult(WORKER_LOCK, True, "HELD")
            lost = True
            logger.error("worker lock lost; stopping so a second worker can never run alongside this one")
            _alert_lock_lost(services)
            if not stopping:
                stopping = True
                scheduler.shutdown(wait=False)  # called from a job thread: never wait for itself
            return JobResult(WORKER_LOCK, False, "LOCK_LOST")

        for spec in (*schedule, worker_lock_schedule()):
            func = watch_lock if spec.job_id == WORKER_LOCK else jobs.runner(spec.job_id)
            scheduler.add_job(func, spec.trigger, id=spec.job_id, name=spec.job_id,
                              max_instances=1, coalesce=True, misfire_grace_time=MISFIRE_GRACE_SECONDS)
        if install_signal_handlers:
            def stop(signum: int, frame: FrameType | None) -> None:
                nonlocal stopping
                if stopping:
                    logger.info("signal %s received again; already shutting down", signum)
                    return  # a second signal, or one racing the lock-loss shutdown: never shut down twice
                stopping = True
                logger.info("signal %s received; waiting for running jobs and shutting down", signum)
                scheduler.shutdown(wait=True)

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
        scheduler.start()
        return EXIT_LOCK_LOST if lost else EXIT_OK
    finally:
        if lock is not None:
            try:
                release_worker_lock(lock)
            except Exception as exc:  # noqa: BLE001 - closing the services below must still happen
                logger.warning("could not release the worker lock: %s", type(exc).__name__)
        services.close()
