"""Worker process lifecycle (D20, D39): watched process lock, job registration, graceful shutdown, services closed."""

from __future__ import annotations

import logging
import signal
import socket
import threading
from collections.abc import Callable
from types import FrameType
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from virtual_orders.alerts.outbox import alert_envelope
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.ledger.worker_sessions import StopReason, host_fingerprint, record_worker_start, record_worker_stop
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
EXIT_DATABASE_UNAVAILABLE = 4  # D49 (M4): the database was unreachable when the worker tried to take its lock
EXIT_UNCAUGHT = 1  # D62: the interpreter's status for an uncaught exception; recorded, never returned

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


class ShutdownGuard:
    """One shutdown per process (D49). `acquire(blocking=False)` is atomic across the lock-watch thread and the
    signal handler, and a signal that interrupts a handler never blocks: the nested claim simply loses."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def claim(self) -> bool:
        return self._lock.acquire(blocking=False)


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


def _hostname() -> str | None:
    try:
        return socket.gethostname()
    except OSError:
        return None


def _start_session(services: Services, hostname: Callable[[], str | None]) -> UUID | None:
    """D62: best effort. A failed write is logged by type only and never stops the worker."""
    session_id = uuid4()
    try:
        record_worker_start(services.engine, session_id=session_id, code_version=services.code_version,
                            fingerprint=host_fingerprint(hostname()))
    except Exception as exc:  # noqa: BLE001 - observation data is never in the critical path
        logger.warning("worker session start not recorded: %s", type(exc).__name__)
        return None
    return session_id


def _stop_session(services: Services, session_id: UUID, exit_code: int, reason: StopReason) -> None:
    try:
        record_worker_stop(services.engine, session_id=session_id, exit_code=exit_code, reason=reason)
    except Exception as exc:  # noqa: BLE001 - the lock is still released and the services still closed
        logger.warning("worker session stop not recorded: %s", type(exc).__name__)


def run_worker(
    services: Services,
    *,
    scheduler_factory: Callable[[], Scheduler] = blocking_scheduler,
    install_signal_handlers: bool = True,
    hostname: Callable[[], str | None] = _hostname,
) -> int:
    lock: Connection | None = None
    lost = False
    signalled = False
    session: UUID | None = None
    ended: tuple[int, StopReason] | None = None
    guard = ShutdownGuard()  # T16 + D49: a second SIGTERM/SIGINT, or a signal after lock loss, never shuts down twice
    try:
        try:
            lock = acquire_worker_lock(services.engine)
        except SQLAlchemyError as exc:  # D49 (M4): one log line with the type, a distinct exit code, no traceback
            logger.error("database unavailable at worker start: %s", type(exc).__name__)
            return EXIT_DATABASE_UNAVAILABLE
        if lock is None:
            logger.error("another worker holds the worker lock; exiting")
            return EXIT_LOCKED
        held: Connection = lock
        session = _start_session(services, hostname)  # D62: only a process holding the lock is a session
        jobs = WorkerJobs(services)
        schedule = build_schedule(services.eval_interval_minutes)
        scheduler = scheduler_factory()

        def watch_lock() -> JobResult:
            nonlocal lost
            if worker_lock_held(held):
                return JobResult(WORKER_LOCK, True, "HELD")
            lost = True
            logger.error("worker lock lost; stopping so a second worker can never run alongside this one")
            _alert_lock_lost(services)
            if guard.claim():
                scheduler.shutdown(wait=False)  # called from a job thread: never wait for itself
            return JobResult(WORKER_LOCK, False, "LOCK_LOST")

        for spec in (*schedule, worker_lock_schedule()):
            func = watch_lock if spec.job_id == WORKER_LOCK else jobs.runner(spec.job_id)
            scheduler.add_job(func, spec.trigger, id=spec.job_id, name=spec.job_id,
                              max_instances=1, coalesce=True, misfire_grace_time=MISFIRE_GRACE_SECONDS)
        if install_signal_handlers:
            def stop(signum: int, frame: FrameType | None) -> None:
                nonlocal signalled
                if not guard.claim():
                    logger.info("signal %s received again; already shutting down", signum)
                    return  # a second signal, or one racing the lock-loss shutdown: never shut down twice
                signalled = True
                logger.info("signal %s received; waiting for running jobs and shutting down", signum)
                scheduler.shutdown(wait=True)

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
        scheduler.start()
        code = EXIT_LOCK_LOST if lost else EXIT_OK
        reason = StopReason.LOCK_LOST if lost else StopReason.SIGNAL if signalled else StopReason.SCHEDULER_STOPPED
        ended = (code, reason)
        return code
    except BaseException:
        ended = (EXIT_UNCAUGHT, StopReason.UNCAUGHT_EXCEPTION)  # also KeyboardInterrupt/SystemExit after the lock
        raise
    finally:
        if session is not None and ended is not None:
            _stop_session(services, session, *ended)  # before the lock is released: the next start sorts after it
        if lock is not None:
            try:
                release_worker_lock(lock)
            except Exception as exc:  # noqa: BLE001 - closing the services below must still happen
                logger.warning("could not release the worker lock: %s", type(exc).__name__)
        services.close()
