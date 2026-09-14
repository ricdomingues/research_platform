from datetime import UTC, date, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration.support import CODE_VERSION, alembic_config, count, submit_default
from virtual_orders.ledger.runs import RunKind, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.storage import tables

NOW = datetime(2025, 11, 26, 21, 0, tzinfo=UTC)
CONSTRAINT = "data_quality_rechecks_order_session_key"


def recheck_row(conn, order_id, run_id, session_date, key):
    conn.execute(tables.data_quality_rechecks.insert().values(
        order_id=order_id, session_date=session_date, recheck_key=key, run_id=run_id, source_run_id=run_id,
        data_as_of=NOW, payload={},
    ))


def constraints_and_indexes(engine):
    with engine.connect() as conn:
        names = set(conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'data_quality_rechecks'::regclass"
        )).scalars())
        indexes = set(conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'data_quality_rechecks'"
        )).scalars())
    return names, indexes


def test_a_second_recheck_row_for_the_same_order_and_session_is_rejected(engine):
    order_id = submit_default(engine).auto_order_id
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.QUALITY_RECHECK, as_of, CODE_VERSION)
        recheck_row(conn, order_id, run.run_id, date(2025, 11, 25), "DATA_QUALITY_RECHECK:2025-11-25:a")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            recheck_row(conn, order_id, run.run_id, date(2025, 11, 25), "DATA_QUALITY_RECHECK:2025-11-25:b")
    with engine.begin() as conn:
        recheck_row(conn, order_id, run.run_id, date(2025, 11, 26), "DATA_QUALITY_RECHECK:2025-11-26:a")
    assert count(engine, "data_quality_rechecks") == 2


def test_downgrade_to_0003_and_back(database_url, engine):
    config = alembic_config(database_url)
    command.downgrade(config, "0003")
    names, indexes = constraints_and_indexes(engine)
    assert CONSTRAINT not in names and "data_quality_rechecks_session_idx" in indexes
    command.upgrade(config, "head")
    names, indexes = constraints_and_indexes(engine)
    assert CONSTRAINT in names and "data_quality_rechecks_session_idx" not in indexes
