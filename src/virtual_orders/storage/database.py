"""Engine construction and advisory-lock keys (Postgres only)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

CYCLE_LOCK_KEY = 0x564F0001  # global evaluator cycle lock (spec 5.2)
INGEST_LOCK_KEY = 0x564F0002  # serializes ingestion and data_as_of watermarks (decision 3)
WORKER_LOCK_KEY = 0x564F0003  # one worker process per database (D20), held for the process lifetime


CONNECT_TIMEOUT_SECONDS = 5  # libpq connect_timeout: an unreachable host must fail fast, never hang /health


def make_engine(url: str) -> Engine:
    return create_engine(
        url, pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC", "connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )
