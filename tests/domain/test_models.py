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


def test_order_state_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        OrderState(opened_at=datetime(2025, 11, 25, 15, 0))


def test_event_rejects_bar_ts_with_seconds():
    with pytest.raises(ValueError):
        Event(EventType.FILLED, "FILLED", et("2025-11-25", "10:00", 5))


def test_datetimes_are_normalized_to_utc():
    from datetime import date, timezone
    from zoneinfo import ZoneInfo

    from core.domain.calendar import Session

    ny = ZoneInfo("America/New_York")
    open_et = datetime(2025, 11, 25, 9, 30, tzinfo=ny)
    close_et = datetime(2025, 11, 25, 16, 0, tzinfo=ny)
    session = Session(date(2025, 11, 25), open_et, close_et)
    assert session.open_utc.tzinfo is timezone.utc and session.close_utc.tzinfo is timezone.utc
    assert session.open_utc == open_et

    # valid_until_ts must be a loaded session close (R3); 2025-11-27 is Thanksgiving.
    ctx = OrderContext(long_signal(), FillConfig(), make_calendar(), open_et,
                       datetime(2025, 11, 26, 16, 0, tzinfo=ny))
    assert ctx.evaluation_start_ts.tzinfo is timezone.utc
    assert ctx.valid_until_ts.tzinfo is timezone.utc

    names = ("entry_eligible_from", "trigger_hit_at", "stop_active_from", "opened_at",
             "closed_at", "final_event_ts", "last_bar_ts")
    state = OrderState(**{name: open_et for name in names})
    for name in names:
        assert getattr(state, name).tzinfo is timezone.utc
        assert getattr(state, name) == open_et

    event = Event(EventType.FILLED, "FILLED", open_et)
    assert event.bar_ts.tzinfo is timezone.utc
    assert Bar(open_et, D(1), D(1), D(1), D(1)).ts.tzinfo is timezone.utc


def test_position_metrics_ignore_ambient_decimal_context():
    from decimal import Context, localcontext

    state = OrderState(avg_entry=D(101), initial_stop=D("97.9"), best_price=D(103), realized_pnl=D(1))
    expected_r = r_multiple(state, D(3))
    expected_excursion = excursion_r(state, Direction.LONG)
    assert expected_r == Decimal("0.3333333333333333333333333333")
    with localcontext(Context(prec=12)):
        assert r_multiple(state, D(3)) == expected_r
        assert excursion_r(state, Direction.LONG) == expected_excursion


SIGNAL_MONEY = ("entry_zone_low", "entry_zone_high", "stop", "target1", "target2", "trigger_price")
CONFIG_MONEY = (
    "risk_amount", "entry_slippage_bps", "stop_slippage_bps", "commission_per_execution",
    "sec_fee_rate", "taf_fee_per_share", "taf_fee_max", "target1_scale_out_pct",
    "crosscheck_tolerance_pct", "dividend_tolerance",
)


def test_signal_money_fields_int_and_decimal_hash_identically():
    from core.domain.hashing import sha256_hex
    from core.fills.v1 import new_order_state
    from tests.support import make_ctx

    ints = SignalSpec("AAPL", Direction.LONG, 100, 102, 97, 106, 110, 104, 3)
    decimals = SignalSpec("AAPL", Direction.LONG, D(100), D(102), D(97), D(106), D(110), D(104), 3)
    strings = SignalSpec("AAPL", Direction.LONG, "100", "102.00", "97", "106", "110", "104", 3)
    assert ints == decimals == strings
    for name in SIGNAL_MONEY:
        assert type(getattr(ints, name)) is Decimal
    assert sha256_hex(ints.as_payload()) == sha256_hex(decimals.as_payload()) == sha256_hex(strings.as_payload())
    created_int = new_order_state(make_ctx(ints)).events[0]
    created_dec = new_order_state(make_ctx(decimals)).events[0]
    assert created_int.payload_hash == created_dec.payload_hash
    assert SignalSpec("AAPL", Direction.LONG, 100, 102, 97, 106).target2 is None


@pytest.mark.parametrize("name", SIGNAL_MONEY)
@pytest.mark.parametrize("bad", [100.0, True])
def test_signal_rejects_float_and_bool_money(name, bad):
    values = dict(ticker="AAPL", direction=Direction.LONG, entry_zone_low=D(100), entry_zone_high=D(102),
                  stop=D(97), target1=D(106))
    values[name] = bad
    with pytest.raises(TypeError, match=name):
        SignalSpec(**values)


def test_signal_rejects_bad_strings_and_valid_sessions_types():
    with pytest.raises(ValueError, match="stop"):
        long_signal(stop="abc")
    with pytest.raises(ValueError, match="stop"):
        long_signal(stop="NaN")
    with pytest.raises(ValueError, match="stop"):
        long_signal(stop=Decimal("Infinity"))
    with pytest.raises(TypeError, match="valid_sessions"):
        long_signal(valid_sessions=True)
    with pytest.raises(TypeError, match="valid_sessions"):
        long_signal(valid_sessions=2.0)


def test_fill_config_int_and_decimal_hash_identically():
    from core.domain.hashing import sha256_hex
    from core.fills.v1 import new_order_state
    from tests.support import make_ctx

    as_ints = FillConfig(risk_amount=100, stop_slippage_bps=5, target1_scale_out_pct=50)
    assert as_ints == FillConfig()
    assert type(as_ints.risk_amount) is Decimal
    assert sha256_hex(as_ints.snapshot()) == sha256_hex(FillConfig().snapshot())
    assert (new_order_state(make_ctx(config=as_ints)).events[0].payload_hash
            == new_order_state(make_ctx(config=FillConfig())).events[0].payload_hash)


@pytest.mark.parametrize("name", CONFIG_MONEY)
def test_fill_config_rejects_float_and_bool_money(name):
    with pytest.raises(TypeError, match=name):
        FillConfig(**{name: 0.1})
    with pytest.raises(TypeError, match=name):
        FillConfig(**{name: True})


def test_fill_config_rejects_bad_int_and_bool_fields():
    with pytest.raises(TypeError, match="data_gap_minutes"):
        FillConfig(data_gap_minutes=True)
    with pytest.raises(TypeError, match="data_gap_minutes"):
        FillConfig(data_gap_minutes=30.0)
    with pytest.raises(TypeError, match="sec_taf_fees_enabled"):
        FillConfig(sec_taf_fees_enabled=1)


def test_fill_config_snapshot_round_trips_through_canonical_json():
    import json

    from core.domain.hashing import canonical_json, sha256_hex
    from core.domain.models import ZoneLostPolicy

    config = FillConfig(
        risk_amount=D("250.50"), entry_slippage_bps=D("2.5"), commission_per_execution=D("0.35"),
        sec_taf_fees_enabled=True, sec_fee_rate=D("0.0000278"), taf_fee_per_share=D("0.000166"),
        taf_fee_max=D("8.30"), zone_lost_policy=ZoneLostPolicy.CANCEL, data_gap_minutes=15,
    )
    for snapshot in (config.snapshot(), json.loads(canonical_json(config.snapshot()))):
        rebuilt = FillConfig(**snapshot)
        assert rebuilt == config
        assert sha256_hex(rebuilt.snapshot()) == sha256_hex(config.snapshot())


@pytest.mark.parametrize(
    "name",
    ["sec_fee_rate", "taf_fee_per_share", "taf_fee_max", "crosscheck_tolerance_pct", "dividend_tolerance"],
)
def test_fill_config_rejects_negative_fee_and_tolerance_fields(name):
    with pytest.raises(ValueError, match=name):
        FillConfig(**{name: D("-0.01")})
    FillConfig(**{name: D(0)})
