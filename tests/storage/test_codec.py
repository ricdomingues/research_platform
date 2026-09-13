import json
from dataclasses import fields, replace
from uuid import uuid4

import pytest

from core.domain.models import (
    CloseReason,
    Direction,
    EntryPath,
    Event,
    EventType,
    FillConfig,
    OrderState,
    OrderStatus,
    ZoneLostPolicy,
)
from core.fills import get_fill_model
from tests.support import D, bar, et, flat_bars, long_signal, make_ctx
from virtual_orders.storage import codec

V1 = get_fill_model("v1")
DAY = "2025-11-25"


def _partial_state() -> OrderState:
    ctx = make_ctx()
    bars = (
        flat_bars(ctx.calendar, et(DAY, "09:30"), et(DAY, "10:05"), 105)
        + [bar(et(DAY, "10:05"), 101, 101.5, 100.5, 101.2)]
        + flat_bars(ctx.calendar, et(DAY, "10:06"), et(DAY, "11:00"), 103)
        + [bar(et(DAY, "11:00"), 105, 106, 104.8, 105.5)]
    )
    state = V1.run_bars(V1.new_order_state(ctx).state, bars, ctx).state
    assert state.status is OrderStatus.PARTIAL
    return V1.freeze(state, "SPLIT", "2025-11-26").state


def _roundtrip(state: OrderState) -> OrderState:
    stored = json.loads(json.dumps(codec.state_to_document(state)))  # simulates jsonb
    return codec.state_from_document(stored)


def test_engine_state_roundtrips_exactly():
    state = _partial_state()
    assert state.stop_previous is not None and state.stop_active_from is not None
    assert state.frozen_reasons == ("SPLIT",) and state.review_reasons == ("SPLIT",)
    restored = _roundtrip(state)
    assert restored == state
    assert codec.state_to_document(restored) == codec.state_to_document(state)


def test_every_field_populated_roundtrips():
    ts = et(DAY, "12:00")
    state = replace(
        _partial_state(),
        status=OrderStatus.CLOSED, zone_lost=True, zone_ever_lost=True, entry_eligible_from=ts,
        trigger_hit_at=ts, entry_path=EntryPath.RECLAIMED, t1_done=True, dividends=D("0.26"),
        worst_price=D("99.5"), closed_at=ts, final_event_ts=ts, last_bar_ts=ts,
        close_reason=CloseReason.TARGET_FINAL, review_reasons=("B", "A"),
    )
    document = codec.state_to_document(state)
    assert all(value is not None for value in document.values())
    assert _roundtrip(state) == state
    assert _roundtrip(state).review_reasons == ("B", "A")


def test_every_order_state_field_is_mapped():
    document = codec.state_to_document(OrderState())
    assert set(document) == {f.name for f in fields(OrderState)}
    assert codec.state_from_document(document) == OrderState()


def test_document_with_missing_or_extra_field_is_rejected():
    document = codec.state_to_document(OrderState())
    missing = {k: v for k, v in document.items() if k != "t1_done"}
    with pytest.raises(ValueError, match="t1_done"):
        codec.state_from_document(missing)
    with pytest.raises(ValueError, match="bogus"):
        codec.state_from_document({**document, "bogus": 1})


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError):
        codec.parse_ts("2025-11-25T15:00:00")


def test_config_snapshot_roundtrips_through_json():
    config = FillConfig(
        risk_amount=D("250.50"), stop_slippage_bps=D(7), commission_per_execution=D("1.25"),
        sec_taf_fees_enabled=True, sec_fee_rate=D("0.0000278"), zone_lost_policy=ZoneLostPolicy.CANCEL,
        target1_scale_out_pct=D(40), data_gap_minutes=15,
    )
    snapshot = json.loads(json.dumps(codec.config_to_snapshot(config)))
    restored = codec.config_from_snapshot(snapshot)
    assert restored == config
    assert codec.config_to_snapshot(restored) == codec.config_to_snapshot(config)


def test_prepared_event_keeps_hash_and_string_payload():
    batch = uuid4()
    event = Event(
        EventType.FILLED, "FILLED", et(DAY, "10:05"), price=D("101.00"), qty=D("25"),
        bar_batch_id=batch, payload={"rule": "ZONE_OPEN", "raw_price": D("101"), "cost": D(0)},
    )
    prepared = codec.prepare_event(event)
    assert prepared.payload_hash == event.payload_hash
    assert prepared.identity() == ("FILLED", event.payload_hash)
    assert codec.material_hash(prepared.hash_material) == event.payload_hash
    assert prepared.payload == {"rule": "ZONE_OPEN", "raw_price": "101", "cost": "0"}
    assert prepared.type == "FILLED" and prepared.bar_batch_id == batch


def test_short_signal_payload_document_is_float_free():
    document = codec.to_document(long_signal(direction=Direction.SHORT, entry_zone_low=D(100),
                                             entry_zone_high=D(102), stop=D(105), target1=D(95),
                                             target2=D(90)).as_payload())
    assert document["stop"] == "105"
    assert not any(isinstance(value, float) for value in document.values())
