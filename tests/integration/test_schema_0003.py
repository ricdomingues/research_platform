from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.integration.conftest import alembic_config
from tests.integration.support import CODE_VERSION, count, submit_default
from virtual_orders.ledger.runs import RunKind, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.storage import tables

NOW = datetime(2025, 11, 25, 15, 0, tzinfo=UTC)


def rule(**overrides):
    values = dict(id=uuid4(), ticker="AAPL", kind="PRICE_CROSS", level=Decimal("101"), direction="ABOVE",
                  cmf_threshold=None, window_bars=None, cooldown_minutes=30, created_at=NOW)
    values.update(overrides)
    return values


def test_new_run_kinds_are_accepted(engine):
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        start_run(conn, RunKind.QUALITY_RECHECK, as_of, CODE_VERSION)
        start_run(conn, RunKind.WATCHLIST, as_of, CODE_VERSION)
    assert count(engine, "evaluation_runs") == 2


@pytest.mark.parametrize("bad", [
    {"level": None},
    {"direction": None},
    {"level": Decimal("0")},
    {"cmf_threshold": Decimal("0.5")},
    {"kind": "PRESSURE"},
    {"kind": "PRESSURE", "level": None, "direction": None, "cmf_threshold": Decimal("1"), "window_bars": 20},
    {"kind": "PRESSURE", "level": None, "direction": None, "cmf_threshold": Decimal("0.3"), "window_bars": 4},
    {"cooldown_minutes": 1441},
    {"kind": "VOLUME"},
])
def test_alert_rule_checks_reject_inconsistent_definitions(engine, bad):
    with engine.begin() as conn:
        conn.execute(tables.watchlist.insert().values(ticker="AAPL", added_at=NOW))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.alert_rules.insert().values(**rule(**bad)))


def test_removing_a_watchlist_ticker_removes_its_rules(engine):
    with engine.begin() as conn:
        conn.execute(tables.watchlist.insert().values(ticker="AAPL", added_at=NOW))
        conn.execute(tables.alert_rules.insert().values(**rule()))
        conn.execute(tables.alert_rules.insert().values(**rule(
            kind="PRESSURE", level=None, direction=None, cmf_threshold=Decimal("0.3"), window_bars=20)))
        conn.execute(tables.watchlist.delete().where(tables.watchlist.c.ticker == "AAPL"))
    assert count(engine, "alert_rules") == 0


def test_outbox_keys_are_unique(engine):
    values = dict(alert_key="ORDER_EVENT:x:FILLED", kind="ORDER_EVENT", subject="x", subject_ts=None, document={})
    with engine.begin() as conn:
        conn.execute(tables.alert_outbox.insert().values(**values))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.alert_outbox.insert().values(**values))


def test_recheck_keys_are_unique_per_order(engine):
    order_id = submit_default(engine).auto_order_id
    as_of = acquire_data_as_of(engine)
    with engine.begin() as conn:
        run = start_run(conn, RunKind.QUALITY_RECHECK, as_of, CODE_VERSION)
    values = dict(order_id=order_id, session_date=date(2025, 11, 25), recheck_key="DATA_QUALITY_RECHECK:k",
                  run_id=run.run_id, source_run_id=run.run_id, data_as_of=as_of, payload={})
    with engine.begin() as conn:
        conn.execute(tables.data_quality_rechecks.insert().values(**values))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.data_quality_rechecks.insert().values(**values))


def test_delivery_outcomes_include_expiry(engine):
    with engine.begin() as conn:
        alert_id = conn.execute(tables.alert_outbox.insert().values(
            alert_key="HEALTH:1", kind="HEALTH", subject=None, subject_ts=None, document={},
        ).returning(tables.alert_outbox.c.id)).scalar_one()
        conn.execute(tables.alert_delivery_attempts.insert().values(alert_id=alert_id, outcome="EXPIRED"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(tables.alert_delivery_attempts.insert().values(alert_id=alert_id, outcome="GIVEN_UP"))


def test_new_history_tables_are_append_only():
    assert {
        "data_quality_rechecks", "alert_outbox", "alert_delivery_attempts", "alert_event_marks", "health_state_log",
    } <= set(tables.APPEND_ONLY_TABLES)
    assert "watchlist" not in tables.APPEND_ONLY_TABLES and "alert_rules" not in tables.APPEND_ONLY_TABLES


def test_downgrade_to_0002_and_back(database_url, engine):
    config = alembic_config(database_url)
    command.downgrade(config, "0002")
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(tables.health_state_log)).scalar_one() == 0
