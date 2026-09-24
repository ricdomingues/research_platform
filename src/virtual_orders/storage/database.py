"""Engine construction and advisory-lock keys (Postgres only)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

CYCLE_LOCK_KEY = 0x564F0001  # global evaluator cycle lock (spec 5.2)
INGEST_LOCK_KEY = 0x564F0002  # serializes ingestion and data_as_of watermarks (decision 3)
WORKER_LOCK_KEY = 0x564F0003  # one worker process per database (D20), held for the process lifetime
RESEARCH_DATASET_LOCK_KEY = 0x564F0004  # serializes dataset family creation and revision numbering


CONNECT_TIMEOUT_SECONDS = 5  # libpq connect_timeout: an unreachable host must fail fast, never hang /health
# D49 (M2): a half-open TCP session must fail instead of hanging the worker_lock job forever.
TCP_KEEPALIVE_ARGS: dict[str, int] = {
    "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
    "tcp_user_timeout": 60000,
}


def make_engine(url: str) -> Engine:
    return create_engine(
        url, pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC", "connect_timeout": CONNECT_TIMEOUT_SECONDS, **TCP_KEEPALIVE_ARGS},
    )
