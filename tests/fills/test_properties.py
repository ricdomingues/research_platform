from datetime import timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from core.domain.calendar import evaluation_start_ts, signal_valid_until_ts
from core.domain.models import (
    MARKET_EVENT_TYPES, Bar, Direction, EventType, FillConfig, OrderContext, OrderStatus, SignalSpec,
)
from core.domain.position import r_multiple
from core.fills.v1 import new_order_state, run_bars, step
from tests.support import et, make_calendar

CAL = make_calendar()
MINUTES = CAL.expected_minutes(et("2025-11-24", "09:30"), et("2025-12-03", "16:00"))
FIRST_THREE_SESSIONS = 390 * 3
MIRROR_K = Decimal(400)

PROPERTY_SETTINGS = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


def cents(value: int) -> Decimal:
    return Decimal(value) / 100


@st.composite
def long_signals(draw):
    zone_low = draw(st.integers(9800, 10100))
    zone_high = zone_low + draw(st.integers(0, 150))
    stop = zone_low - draw(st.integers(30, 300))
    target1 = zone_high + draw(st.integers(30, 300))
    target2 = draw(st.one_of(st.none(), st.integers(30, 300).map(lambda d: target1 + d)))
    trigger = draw(st.one_of(st.none(), st.integers(9700, 10400)))
    return SignalSpec(
        ticker="AAPL",
        direction=Direction.LONG,
        entry_zone_low=cents(zone_low),
        entry_zone_high=cents(zone_high),
        stop=cents(stop),
        target1=cents(target1),
        target2=None if target2 is None else cents(target2),
        trigger_price=None if trigger is None else cents(trigger),
        valid_sessions=draw(st.integers(1, 3)),
    )


@st.composite
def bar_paths(draw):
    start = draw(st.integers(0, len(MINUTES) - 1))
    price = draw(st.integers(9700, 10400))
    steps = draw(
        st.lists(
            st.tuples(
                st.integers(-80, 80), st.integers(-120, 120),
                st.integers(0, 80), st.integers(0, 80), st.booleans(),
            ),
            min_size=1,
            max_size=150,
        )
    )
    bars = []
    for offset, (gap, move, up, down, keep) in enumerate(steps):
        index = start + offset
        if index >= len(MINUTES):
            break
        open_ = price + gap
        close = open_ + move
        high = max(open_, close) + up
        low = min(open_, close) - down
        price = close
        if keep:
            bars.append(Bar(MINUTES[index], cents(open_), cents(high), cents(low), cents(close), Decimal(100)))
    return bars


@st.composite
def scenarios(draw, zero_costs: bool = False):
    signal = draw(long_signals())
    created = MINUTES[draw(st.integers(0, FIRST_THREE_SESSIONS - 1))] + timedelta(
        seconds=draw(st.integers(0, 59))
    )
    begin = evaluation_start_ts(CAL, created)
    if zero_costs:
        config = FillConfig(stop_slippage_bps=Decimal(0))
    else:
        config = FillConfig(
            entry_slippage_bps=Decimal(draw(st.integers(0, 5))),
            stop_slippage_bps=Decimal(draw(st.integers(0, 10))),
            commission_per_execution=cents(draw(st.integers(0, 100))),
        )
    ctx = OrderContext(signal, config, CAL, begin, signal_valid_until_ts(CAL, begin, signal.valid_sessions))
    return ctx, draw(bar_paths())


def run(ctx, bars):
    return run_bars(new_order_state(ctx).state, bars, ctx)


@PROPERTY_SETTINGS
@given(scenarios())
def test_deterministic_events_and_hashes(scenario):
    ctx, bars = scenario
    first, second = run(ctx, bars), run(ctx, bars)
    assert first.events == second.events
    assert [e.payload_hash for e in first.events] == [e.payload_hash for e in second.events]
    assert first.state == second.state


@PROPERTY_SETTINGS
@given(scenarios(), st.data())
def test_prefix_no_look_ahead(scenario, data):
    ctx, bars = scenario
    cut = data.draw(st.integers(0, len(bars)))
    full = run(ctx, bars).events
    prefix = run(ctx, bars[:cut]).events
    assert full[: len(prefix)] == prefix


@PROPERTY_SETTINGS
@given(scenarios())
def test_market_events_inside_evaluation_window(scenario):
    ctx, bars = scenario
    for event in run(ctx, bars).events:
        if event.type in MARKET_EVENT_TYPES:
            assert ctx.evaluation_start_ts <= event.bar_ts < ctx.valid_until_ts


@PROPERTY_SETTINGS
@given(scenarios())
def test_accounting_invariants(scenario):
    ctx, bars = scenario
    state = new_order_state(ctx).state
    events = []
    for current in bars:
        result = step(state, current, ctx)
        state = result.state
        events.extend(result.events)
        assert state.qty_open >= 0
        assert state.qty_open <= state.qty_total
    assert sum((e.payload.get("pnl", Decimal(0)) for e in events), Decimal(0)) == state.realized_pnl
    assert sum((e.payload.get("cost", Decimal(0)) for e in events), Decimal(0)) == state.costs
    if state.status is OrderStatus.CLOSED:
        assert state.qty_open == 0


@PROPERTY_SETTINGS
@given(scenarios())
def test_reprocessing_is_idempotent(scenario):
    ctx, bars = scenario
    first = run(ctx, bars)
    again = run_bars(first.state, bars, ctx)
    assert again.events == ()
    assert again.state == first.state


@PROPERTY_SETTINGS
@given(scenarios())
def test_event_keys_unique_and_no_fill_on_reclaim_candle(scenario):
    ctx, bars = scenario
    events = run(ctx, bars).events
    keys = [e.event_key for e in events]
    assert len(keys) == len(set(keys))
    reclaimed = {e.bar_ts for e in events if e.type is EventType.ZONE_RECLAIMED}
    filled = {e.bar_ts for e in events if e.type is EventType.FILLED}
    assert not reclaimed & filled


def _mirror_price(value):
    return None if value is None else MIRROR_K - value


@PROPERTY_SETTINGS
@given(scenarios(zero_costs=True))
def test_short_mirror_matches_long(scenario):
    ctx, bars = scenario
    s = ctx.signal
    short_signal = SignalSpec(
        ticker=s.ticker,
        direction=Direction.SHORT,
        entry_zone_low=MIRROR_K - s.entry_zone_high,
        entry_zone_high=MIRROR_K - s.entry_zone_low,
        stop=MIRROR_K - s.stop,
        target1=MIRROR_K - s.target1,
        target2=_mirror_price(s.target2),
        trigger_price=_mirror_price(s.trigger_price),
        valid_sessions=s.valid_sessions,
    )
    short_ctx = OrderContext(short_signal, ctx.config, CAL, ctx.evaluation_start_ts, ctx.valid_until_ts)
    short_bars = [
        Bar(b.ts, MIRROR_K - b.open, MIRROR_K - b.low, MIRROR_K - b.high, MIRROR_K - b.close, b.volume)
        for b in bars
    ]
    long_result = run(ctx, bars)
    short_result = run(short_ctx, short_bars)
    assert [e.type for e in long_result.events] == [e.type for e in short_result.events]
    assert [e.bar_ts for e in long_result.events] == [e.bar_ts for e in short_result.events]
    for long_event, short_event in zip(long_result.events, short_result.events):
        assert short_event.price == _mirror_price(long_event.price)
        assert short_event.qty == long_event.qty
    risk = ctx.config.risk_amount
    assert r_multiple(long_result.state, risk) == r_multiple(short_result.state, risk)
