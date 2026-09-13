from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from core.domain.hashing import sha256_hex
from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    TICKER,
    FakeBarSource,
    count,
    feeds,
    flat_raw,
    raw,
    scenario_bars,
    submit_default,
)
from tests.support import et
from virtual_orders.evaluator.coverage import CoverageDecision
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.manual import ManualOrderError, actionability_outcomes, create_manual_order
from virtual_orders.ledger.events import stored_events
from virtual_orders.ledger.orders import get_order, get_signal, read_projection_row
from virtual_orders.ledger.runs import RunStatus, latest_run_status
from virtual_orders.storage import tables


def manual(engine, signal_id, source, hm, second=0, day=DAY, **kwargs):
    return create_manual_order(engine, signal_id, config=FillConfig(), code_version=CODE_VERSION,
                               price_source=PRICE_SOURCE, created_at=et(day, hm, second),
                               gateway=None if source is None else feeds(source), **kwargs)


def actionability_runs(engine):
    with engine.connect() as conn:
        ids = conn.execute(select(tables.evaluation_runs.c.run_id).where(
            tables.evaluation_runs.c.kind == "ACTIONABILITY")).scalars().all()
        return [latest_run_status(conn, run_id) for run_id in ids]


def test_actionable_signal_creates_manual_order_with_audit(engine):
    signal_id = submit_default(engine).signal_id
    created = manual(engine, signal_id, FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105)), "13:00")
    with engine.connect() as conn:
        order = get_order(conn, created.order_id)
        signal = get_signal(conn, signal_id)
        (event,) = stored_events(conn, order.id)
        row = read_projection_row(conn, order.id)
        status, detail = latest_run_status(conn, created.actionability_run_id)
    payload = event.prepared.payload
    assert order.origin.value == "MANUAL_USER" and order.created_at == et(DAY, "13:00")
    assert order.evaluation_start_ts == et(DAY, "13:00") and order.valid_until_ts == signal.valid_until_ts
    assert payload["partial_bar_skipped"] is False and payload["skipped_bar_ts"] is None
    assert payload["actionability_run_id"] == str(created.actionability_run_id)
    assert payload["inherited_signal_state"]["status"] == "PENDING"
    assert payload["inherited_signal_state_hash"] == sha256_hex(payload["inherited_signal_state"])
    assert payload["actionability_selected_data_hash"] == detail["selected_data_hash"]
    assert status is RunStatus.COMPLETED and detail["result"] == "ACTIONABLE"
    assert detail["coverage_policy"] == "STRICT_PRIMARY" and payload["actionability_coverage_policy"] == "STRICT_PRIMARY"
    assert detail["price_source"] == PRICE_SOURCE and order.price_source == PRICE_SOURCE
    assert detail["bar_from"] == et(DAY, "09:30").isoformat() and detail["bar_to"] == et(DAY, "12:59").isoformat()
    assert row["status"] == "PENDING" and created.partial_bar_skipped is False
    assert payload["actionability_coverage_evidence"] == {}


def test_click_inside_a_minute_skips_the_partial_bar(engine):
    signal_id = submit_default(engine).signal_id
    created = manual(engine, signal_id, FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105)), "13:00", second=18)
    with engine.connect() as conn:
        order = get_order(conn, created.order_id)
        payload = stored_events(conn, order.id)[0].prepared.payload
    assert created.partial_bar_skipped and payload["skipped_bar_ts"] == et(DAY, "13:00").isoformat()
    assert order.evaluation_start_ts == et(DAY, "13:01")


def test_ingest_window_uses_the_single_captured_click_time(engine):
    """T is captured once before any ingest (spec 3.4.1): the fetch ceiling is floor_minute(created_at),
    not a later wall-clock read taken after ingestion (which could drift past a minute boundary)."""
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105))
    manual(engine, signal_id, source, "13:00", second=27)
    assert source.calls
    _, _, end = source.calls[-1]
    assert end == et(DAY, "13:00")


@pytest.mark.parametrize("bars, hm, code, reason", [
    (scenario_bars() + flat_raw(DAY, "12:51", "13:30", 108), "13:00", "SIGNAL_NO_LONGER_ACTIONABLE", "TARGET_REACHED"),
    (scenario_bars(), "10:30", "SIGNAL_NO_LONGER_ACTIONABLE", "ENTRY_OPPORTUNITY_ALREADY_OCCURRED"),
    (flat_raw(DAY, "09:30", "10:20", 105) + [raw(DAY, "10:20", 98, 98.5, 96, 98)] + flat_raw(DAY, "10:21", "13:00", 99),
     "13:00", "SIGNAL_NO_LONGER_ACTIONABLE", "INVALIDATED"),
])
def test_finished_thesis_is_rejected_and_audited(engine, bars, hm, code, reason):
    signal_id = submit_default(engine, auto_order=False).signal_id
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, FakeBarSource(bars), hm)
    assert (caught.value.code, caught.value.reason) == (code, reason)
    assert count(engine, "orders") == 0
    ((status, detail),) = actionability_runs(engine)
    assert status is RunStatus.COMPLETED and detail["result"] == code and detail["reason"] == reason


def test_expired_signal_is_rejected(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, None, "13:00", day="2025-11-28")
    assert (caught.value.code, caught.value.reason) == ("SIGNAL_EXPIRED", None)


def test_expired_signal_is_rejected_before_a_failing_provider_is_even_asked(engine):
    """Expiry is checked before ingestion: a failing provider must never turn a 422 into a 503."""
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105))
    source.failing.add(TICKER)
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, source, "13:00", day="2025-11-28")
    assert (caught.value.code, caught.value.reason) == ("SIGNAL_EXPIRED", None)
    assert source.calls == []
    ((status, detail),) = actionability_runs(engine)
    assert status is RunStatus.COMPLETED and detail["result"] == "SIGNAL_EXPIRED"


def test_missing_minute_makes_actionability_unverifiable(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    bars = [b for b in flat_raw(DAY, "09:30", "13:00", 105) if b.ts != et(DAY, "11:11")]
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, FakeBarSource(bars), "13:00")
    assert caught.value.code == "ACTIONABILITY_UNVERIFIABLE"
    assert caught.value.detail["missing_count"] == 1
    ((status, detail),) = actionability_runs(engine)
    assert status is RunStatus.FAILED and detail["missing_minutes"] == [et(DAY, "11:11").isoformat()]


def test_provider_failure_makes_actionability_unverifiable(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105))
    source.failing.add(TICKER)
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, source, "13:00")
    assert caught.value.code == "ACTIONABILITY_UNVERIFIABLE" and "ingest_error" in caught.value.detail


def test_manual_order_inherits_lost_zone_and_enters_only_after_reclaim(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(
        [raw(DAY, "09:30", 99.5, 99.8, 99, 99.2)] + flat_raw(DAY, "09:31", "11:00", 99)
        + [raw(DAY, "11:00", 99.5, 100.8, 99.4, 100.5), raw(DAY, "11:01", 101, 101.5, 100.6, 101.2)]
        + flat_raw(DAY, "11:02", "11:10", 101)
    )
    created = manual(engine, signal_id, source, "11:00")
    with engine.connect() as conn:
        inherited = stored_events(conn, created.order_id)[0].prepared.payload["inherited_signal_state"]
    assert inherited["zone_lost"] is True

    run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, "11:05"))
    with engine.connect() as conn:
        events = [e.prepared for e in stored_events(conn, created.order_id)]
    assert [e.type for e in events] == ["ORDER_CREATED", "ZONE_RECLAIMED", "FILLED"]
    assert events[2].bar_ts == et(DAY, "11:01") and events[2].payload["entry_path"] == "RECLAIMED"


def test_manual_order_does_not_depend_on_auto_order(engine):
    bars = flat_raw(DAY, "09:30", "13:00", 105)
    with_auto = submit_default(engine, client_signal_id="with-auto").signal_id
    without_auto = submit_default(engine, client_signal_id="without-auto", auto_order=False).signal_id
    first = manual(engine, with_auto, FakeBarSource(bars), "13:00")
    second = manual(engine, without_auto, FakeBarSource(bars), "13:00")
    with engine.connect() as conn:
        a = stored_events(conn, first.order_id)[0].prepared.payload
        b = stored_events(conn, second.order_id)[0].prepared.payload
    assert a["inherited_signal_state_hash"] == b["inherited_signal_state_hash"]
    assert a["actionability_selected_data_hash"] == b["actionability_selected_data_hash"]
    assert isinstance(UUID(a["actionability_run_id"]), UUID)


class OneMinuteShortPolicy:
    """Test double: reports the first expected minute as unresolved and records evidence."""

    name = "TEST_ONE_MINUTE_SHORT"

    def assess(self, *, price_source, ticker, expected, bars):
        return CoverageDecision(False, tuple(expected[:1]), {"checked_source": price_source})


def test_coverage_policy_is_pluggable_and_recorded(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105)), "13:00",
               coverage_policy=OneMinuteShortPolicy())
    assert caught.value.code == "ACTIONABILITY_UNVERIFIABLE"
    ((status, detail),) = actionability_runs(engine)
    assert status is RunStatus.FAILED and detail["coverage_policy"] == "TEST_ONE_MINUTE_SHORT"
    assert detail["coverage_evidence"] == {"checked_source": PRICE_SOURCE}
    assert detail["missing_minutes"] == [et(DAY, "09:30").isoformat()]


class ContractViolatingPolicy:
    """Test double: claims verified=True while still leaving the first expected minute unresolved."""

    name = "TEST_CONTRACT_VIOLATION"

    def assess(self, *, price_source, ticker, expected, bars):
        return CoverageDecision(True, tuple(expected[:1]), {"checked_source": price_source})


def test_coverage_policy_contract_violation_is_treated_as_unverifiable(engine):
    """verified=True must mean no unresolved minutes; a policy that violates this never gets to build an
    order from bars with holes, and the violation itself is recorded (D7)."""
    signal_id = submit_default(engine, auto_order=False).signal_id
    with pytest.raises(ManualOrderError) as caught:
        manual(engine, signal_id, FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105)), "13:00",
               coverage_policy=ContractViolatingPolicy())
    assert caught.value.code == "ACTIONABILITY_UNVERIFIABLE"
    assert count(engine, "orders") == 0
    ((status, detail),) = actionability_runs(engine)
    assert status is RunStatus.FAILED and detail["coverage_policy"] == "TEST_CONTRACT_VIOLATION"
    assert detail["policy_contract_violation"] is True
    assert detail["missing_count"] == 1 and detail["missing_minutes"] == [et(DAY, "09:30").isoformat()]


def test_actionability_outcomes_are_observable(engine):
    signal_id = submit_default(engine, auto_order=False).signal_id
    source = FakeBarSource(flat_raw(DAY, "09:30", "13:00", 105))
    manual(engine, signal_id, source, "13:00")
    with pytest.raises(ManualOrderError):
        manual(engine, signal_id, source, "13:40")  # 13:00-13:39 never delivered
    with engine.connect() as conn:
        counts = actionability_outcomes(conn, since=datetime(2000, 1, 1, tzinfo=UTC))
    assert counts == {"ACTIONABLE": 1, "ACTIONABILITY_UNVERIFIABLE": 1}
