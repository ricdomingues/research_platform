from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from core.domain.models import (
    Bar,
    Direction,
    Event,
    EventType,
    FillConfig,
    OrderContext,
    OrderState,
    OrderStatus,
    SignalSpec,
)
from core.domain.position import excursion_r, r_multiple
from tests.support import D, et, long_signal, make_calendar


def test_bar_validation():
    ts = et("2025-11-25", "10:00")
    with pytest.raises(TypeError):
        Bar(ts, 1.0, D(2), D(1), D(1))
    with pytest.raises(ValueError):
        Bar(datetime(2025, 11, 25, 15, 0), D(1), D(2), D(1), D(1))
    with pytest.raises(ValueError):
        Bar(et("2025-11-25", "10:00", 5), D(1), D(2), D(1), D(1))
    with pytest.raises(ValueError):
        Bar(ts, D(1), D(2), D("1.5"), D(1))


def test_signal_coerces_direction_and_payload():
    signal = SignalSpec("AAPL", "SHORT", D(100), D(102), D(105), D(95))
    assert signal.direction is Direction.SHORT
    assert signal.as_payload()["target2"] is None


def test_fill_config_defaults_and_snapshot():
    config = FillConfig()
    snapshot = config.snapshot()
    assert snapshot["stop_slippage_bps"] == Decimal("5")
    assert snapshot["zone_lost_policy"] == "RECLAIM"
    assert set(snapshot) >= {
        "risk_amount", "entry_slippage_bps", "commission_per_execution",
        "sec_taf_fees_enabled", "target1_scale_out_pct", "data_gap_minutes",
        "crosscheck_tolerance_pct", "dividend_tolerance",
    }
    with pytest.raises(ValueError):
        FillConfig(target1_scale_out_pct=D(100))
    with pytest.raises(ValueError):
        FillConfig(risk_amount=D(0))


def test_order_context_rejects_expired_window():
    start = et("2025-11-25", "10:00")
    with pytest.raises(ValueError):
        OrderContext(long_signal(), FillConfig(), make_calendar(), start, start)


def test_event_requires_bar_ts_for_market_events_and_hashes_canonically():
    with pytest.raises(ValueError):
        Event(EventType.FILLED, "FILLED")
    ts = et("2025-11-25", "10:00")
    batch = UUID("12345678-1234-5678-1234-567812345678")
    a = Event(EventType.FILLED, "FILLED", ts, D("101.50"), D(10), batch, {"rule": "ZONE_OPEN"})
    b = Event(EventType.FILLED, "FILLED", ts, D("101.5"), D(10), batch, {"rule": "ZONE_OPEN"})
    c = Event(EventType.FILLED, "FILLED", ts, D("101.51"), D(10), batch, {"rule": "ZONE_OPEN"})
    assert a.payload_hash == b.payload_hash
    assert a.payload_hash != c.payload_hash


def test_order_state_flags_and_gating():
    state = OrderState(zone_lost=True, trigger_hit_at=et("2025-11-25", "10:00"))
    gating = state.gating_state()
    assert gating.status is OrderStatus.PENDING and gating.zone_lost
    assert gating.as_payload()["trigger_hit_at"] == et("2025-11-25", "10:00")
    assert not state.is_final and not state.needs_review
    assert OrderState(status=OrderStatus.CANCELED).is_final
    assert OrderState(review_reasons=("SPLIT",)).needs_review


def test_position_metrics_long_and_short():
    long_state = OrderState(
        avg_entry=D(101), initial_stop=D(97), best_price=D(109), worst_price=D(99),
        realized_pnl=D(200), costs=D(2), dividends=D(1),
    )
    assert r_multiple(long_state, D(100)) == D("1.99")
    assert excursion_r(long_state, Direction.LONG) == (D(2), D("-0.5"))
    short_state = OrderState(avg_entry=D(100), initial_stop=D(104), best_price=D(92), worst_price=D(102))
    assert excursion_r(short_state, Direction.SHORT) == (D(2), D("-0.5"))
    assert excursion_r(OrderState(), Direction.LONG) == (None, None)
