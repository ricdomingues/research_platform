from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.support import alembic_config, count
from virtual_orders.storage import tables

FINGERPRINT = "0123456789abcdef"


def started(session_id, **overrides):
    values = dict(session_id=session_id, event="STARTED", code_version="sha", host_fingerprint=FINGERPRINT)
    values.update(overrides)
    return values


def stopped(session_id, **overrides):
    values = dict(session_id=session_id, event="STOPPED", exit_code=0, reason="SIGNAL")
    values.update(overrides)
    return values


def test_a_session_has_at_most_one_start_and_one_stop_row(engine):
    session_id = uuid4()
    with engine.begin() as conn:
        conn.execute(tables.worker_sessions.insert().values(**started(session_id)))
        conn.execute(tables.worker_sessions.insert().values(**stopped(session_id)))
        conn.execute(tables.worker_sessions.insert().values(**started(uuid4(), host_fingerprint=None)))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.worker_sessions.insert().values(**stopped(session_id, exit_code=3, reason="LOCK_LOST")))
    assert count(engine, "worker_sessions") == 3


@pytest.mark.parametrize("row", [
    started(uuid4(), code_version=None),
    started(uuid4(), host_fingerprint="worker-host"),
    started(uuid4(), exit_code=0),
    stopped(uuid4(), exit_code=None),
    stopped(uuid4(), reason="OOM"),
    stopped(uuid4(), code_version="sha"),
    {"session_id": uuid4(), "event": "RESTARTED", "code_version": "sha"},
], ids=["start-without-version", "plain-hostname", "start-with-exit-code", "stop-without-exit-code",
        "unknown-reason", "stop-with-version", "unknown-event"])
def test_rows_that_mix_start_and_stop_fields_are_rejected(engine, row):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.worker_sessions.insert().values(**row))


def test_worker_sessions_are_append_only_and_the_app_role_can_insert(engine):
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE vo_app"))
        conn.execute(tables.worker_sessions.insert().values(**started(uuid4())))
    for statement in ("UPDATE worker_sessions SET code_version = 'x'", "DELETE FROM worker_sessions",
                      "TRUNCATE worker_sessions"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(statement))
    assert "worker_sessions" in tables.APPEND_ONLY_TABLES


def test_the_outbox_accepts_the_observation_alert_kind(engine):
    with engine.begin() as conn:
        conn.execute(tables.alert_outbox.insert().values(
            alert_key="OBSERVATION_DAILY:2025-11-25", kind="OBSERVATION_DAILY", document={}))
    assert count(engine, "alert_outbox") == 1


def test_downgrade_to_0004_and_back(database_url, engine):
    config = alembic_config(database_url)
    command.downgrade(config, "0004")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('worker_sessions')::text")).scalar_one() is None
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.alert_outbox.insert().values(alert_key="k", kind="OBSERVATION_DAILY", document={}))
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('worker_sessions')::text")).scalar_one() == "worker_sessions"
