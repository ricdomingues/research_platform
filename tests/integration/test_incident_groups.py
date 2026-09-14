from datetime import timedelta

from sqlalchemy import func, select

from tests.integration.support import submit_default
from virtual_orders.ledger import errors
from virtual_orders.ledger.errors import LedgerIntegrityError
from virtual_orders.ledger.quarantine import record_incident
from virtual_orders.readmodels.incidents import incident_groups, latest_incidents
from virtual_orders.storage import tables


def test_repeated_incidents_are_one_group_per_kind_and_reason(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="second").auto_order_id
    with engine.begin() as conn:
        for order_id in (first, second, first):
            record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=order_id))
        record_incident(conn, LedgerIntegrityError(
            errors.PROJECTION_INTEGRITY_ERROR, "differs", order_id=first, detail={"reason": "PROJECTION_MISMATCH"}))
        now = conn.execute(select(func.clock_timestamp())).scalar_one()
        conn.execute(tables.integrity_incidents.insert().values(
            kind=errors.PROJECTION_MISSING, order_id=second, detail={"message": "old"},
            recorded_at=now - timedelta(days=3)))

    with engine.connect() as conn:
        recent = incident_groups(conn, since=now - timedelta(days=1))
        everything = incident_groups(conn)

    by_kind = {(g.kind, g.reason): g for g in recent}
    missing = by_kind[(errors.PROJECTION_MISSING, None)]
    assert (missing.occurrences, missing.affected_count) == (3, 2)
    assert missing.affected_orders == tuple(sorted((str(first), str(second))))
    assert missing.first_recorded_at <= missing.last_recorded_at
    assert by_kind[(errors.PROJECTION_INTEGRITY_ERROR, "PROJECTION_MISMATCH")].occurrences == 1
    assert {(g.kind, g.reason): g.occurrences for g in everything}[(errors.PROJECTION_MISSING, None)] == 4


def test_latest_incident_per_order_for_a_kind(engine):
    first = submit_default(engine).auto_order_id
    second = submit_default(engine, client_signal_id="second").auto_order_id
    with engine.begin() as conn:
        record_incident(conn, LedgerIntegrityError(
            errors.REPRODUCE_DIVERGENCE, "old", order_id=first, detail={"reason": "OLD", "diff": []}))
        record_incident(conn, LedgerIntegrityError(
            errors.REPRODUCE_DIVERGENCE, "new", order_id=first, detail={"reason": "NEW", "diff": [{"seq": 2}]}))
        record_incident(conn, LedgerIntegrityError(errors.PROJECTION_MISSING, "gone", order_id=second))

    with engine.connect() as conn:
        latest = latest_incidents(conn, kind=errors.REPRODUCE_DIVERGENCE, order_ids=[first, second])
        assert latest_incidents(conn, kind=errors.REPRODUCE_DIVERGENCE, order_ids=[]) == {}

    assert set(latest) == {first}
    assert latest[first].detail["reason"] == "NEW" and latest[first].detail["diff"] == [{"seq": 2}]
    assert latest[first].kind == errors.REPRODUCE_DIVERGENCE and latest[first].incident_id > 0
