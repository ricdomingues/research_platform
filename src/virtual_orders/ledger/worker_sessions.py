"""Worker process sessions (Plan 4, D62): an append-only start row and a best-effort stop row. Never secrets."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Engine, func

from virtual_orders.storage.tables import worker_sessions

FINGERPRINT_LENGTH = 16


class StopReason(StrEnum):
    SIGNAL = "SIGNAL"
    LOCK_LOST = "LOCK_LOST"
    SCHEDULER_STOPPED = "SCHEDULER_STOPPED"
    UNCAUGHT_EXCEPTION = "UNCAUGHT_EXCEPTION"  # any exception after the lock: scheduler, jobs, schedule, factory, exit


def host_fingerprint(hostname: str | None) -> str | None:
    """First 16 hex digits of SHA-256 of the host name: tells container/host instances apart without the name.

    Inside a container the host name is the container id, which changes on every recreate."""
    if not hostname:
        return None
    return hashlib.sha256(hostname.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def record_worker_start(engine: Engine, *, session_id: UUID, code_version: str, fingerprint: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(worker_sessions.insert().values(
            session_id=session_id, event="STARTED", code_version=code_version, host_fingerprint=fingerprint,
            recorded_at=func.clock_timestamp(),
        ))


def record_worker_stop(engine: Engine, *, session_id: UUID, exit_code: int, reason: StopReason) -> None:
    with engine.begin() as conn:
        conn.execute(worker_sessions.insert().values(
            session_id=session_id, event="STOPPED", exit_code=exit_code, reason=reason.value,
            recorded_at=func.clock_timestamp(),
        ))
