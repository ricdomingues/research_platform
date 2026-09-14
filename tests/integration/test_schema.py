from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.support import alembic_config
from virtual_orders.storage import tables
from virtual_orders.storage.tables import APPEND_ONLY_TABLES


def _insert_batch(conn):
    batch_id = uuid4()
    conn.execute(
        tables.bar_batches.insert().values(
            batch_id=batch_id, provider="alpaca", provider_version="t", data_tier="RESEARCH",
            request={}, content_hash="h", ingested_at=datetime(2025, 11, 25, tzinfo=UTC),
        )
    )
    return batch_id


def test_metadata_matches_migrated_columns(engine):
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= set(tables.metadata.tables)
    for name, table in tables.metadata.tables.items():
        db_columns = {column["name"] for column in inspector.get_columns(name)}
        assert db_columns == {column.name for column in table.columns}, name


@pytest.mark.parametrize("statement", [
    "UPDATE bar_batches SET provider = 'x'",
    "DELETE FROM bar_batches",
    "TRUNCATE bar_batches CASCADE",
])
def test_append_only_rejects_mutation(engine, statement):
    with engine.begin() as conn:
        _insert_batch(conn)
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text(statement))


def test_every_append_only_table_has_triggers(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT event_object_table, event_manipulation FROM information_schema.triggers"
        )).all()
    covered = {(table, op) for table, op in rows}
    for table in APPEND_ONLY_TABLES:
        assert (table, "UPDATE") in covered, table
        assert (table, "DELETE") in covered, table
    with engine.connect() as conn:
        truncate = conn.execute(text(
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE (t.tgtype & 32) = 32 AND NOT t.tgisinternal"
        )).scalars().all()
    assert set(APPEND_ONLY_TABLES) <= set(truncate)


def test_projection_table_is_mutable(engine):
    assert "order_state" not in APPEND_ONLY_TABLES
    assert "dividends" not in APPEND_ONLY_TABLES


def test_app_role_cannot_update_history(engine):
    with engine.begin() as conn:
        _insert_batch(conn)
    with pytest.raises(DBAPIError, match="permission denied"):
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE vo_app"))
            conn.execute(text("UPDATE bar_batches SET provider = 'x'"))
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE vo_app"))
        _insert_batch(conn)


def test_bar_primary_key_allows_new_versions_only(engine):
    with engine.begin() as conn:
        first, second = _insert_batch(conn), _insert_batch(conn)
        row = dict(ticker="AAPL", ts=datetime(2025, 11, 25, 14, 30, tzinfo=UTC), open=1, high=1,
                   low=1, close=1, volume=1, source="alpaca_iex")
        conn.execute(tables.bars_1m.insert().values(batch_id=first, **row))
        conn.execute(tables.bars_1m.insert().values(batch_id=second, **row))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.bars_1m.insert().values(batch_id=first, **row))


def test_session_timezone_is_utc(engine):
    with engine.connect() as conn:
        assert conn.execute(text("SHOW timezone")).scalar_one() == "UTC"


def test_provenance_accepts_any_provider_without_migration(engine):
    with engine.begin() as conn:
        batch_id = uuid4()
        conn.execute(tables.bar_batches.insert().values(
            batch_id=batch_id, provider="robinhood", provider_version="mcp-2026-09", data_tier="RESEARCH",
            request={"tool": "bars", "timeframe": "1m"}, content_hash="h",
            ingested_at=datetime(2025, 11, 25, tzinfo=UTC),
        ))
        conn.execute(tables.bars_1m.insert().values(
            ticker="AAPL", ts=datetime(2025, 11, 25, 14, 30, tzinfo=UTC), open=1, high=1, low=1, close=1,
            volume=1, source="robinhood_realtime", batch_id=batch_id,
        ))
        constrained = conn.execute(text(
            "SELECT column_name FROM information_schema.constraint_column_usage u "
            "JOIN information_schema.check_constraints c ON c.constraint_name = u.constraint_name "
            "WHERE u.column_name IN ('provider', 'provider_version', 'source', 'price_source')"
        )).scalars().all()
    assert constrained == []


def test_migrations_run_from_zero_and_back(database_url):
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    probe = create_engine(database_url)
    try:
        assert set(inspect(probe).get_table_names()) <= {"alembic_version"}
        command.upgrade(config, "head")
        assert set(inspect(probe).get_table_names()) >= set(tables.metadata.tables)
    finally:
        probe.dispose()
