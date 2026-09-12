from collections import Counter
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext

from hypothesis import HealthCheck, Phase, find, given, settings
from hypothesis import strategies as st
from hypothesis.errors import NoSuchExample

from core.domain.calendar import evaluation_start_ts, signal_valid_until_ts
from core.domain.models import (
    MARKET_EVENT_TYPES, Bar, Direction, EventType, FillConfig, OrderContext, OrderStatus, SignalSpec,
)
from core.domain.position import r_multiple
from core.fills.v1 import new_order_state, run_bars, step
from core.fills.v1.engine import V1_CONTEXT
from tests.support import et, make_calendar

CAL = make_calendar()
MINUTES = CAL.expected_minutes(et("2025-11-24", "09:30"), et("2025-12-03", "16:00"))
MINUTE_INDEX = {minute: index for index, minute in enumerate(MINUTES)}
FIRST_THREE_SESSIONS = 390 * 3
# Last expected minute of each of the first three (full) sessions.
FIRST_THREE_SESSION_LAST_INDEXES = (389, 779, 1169)
MIRROR_K = Decimal(400)
EXIT_EVENT_TYPES = (EventType.TARGET1_HIT, EventType.TARGET2_HIT, EventType.STOPPED, EventType.TIME_EXIT)

PROPERTY_SETTINGS = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

REACHABILITY_SETTINGS = settings(
    max_examples=400,
    deadline=None,
    database=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
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
def bar_paths(draw, start, zone_low_cents, force_index=None):
    price = zone_low_cents + draw(st.integers(-250, 250))
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
    forced_bar = None
    last_index = start - 1
    for offset, (gap, move, up, down, keep) in enumerate(steps):
        index = start + offset
        if index >= len(MINUTES):
            break
        open_ = price + gap
        close = open_ + move
        high = max(open_, close) + up
        low = min(open_, close) - down
        price = close
        last_index = index
        candidate = Bar(MINUTES[index], cents(open_), cents(high), cents(low), cents(close), Decimal(100))
        if index == force_index:
            # This bar must be present (not subject to the random "keep" drop) so the
            # caller's requested boundary (e.g. the window's last expected minute) is
            # actually reachable instead of merely likely.
            forced_bar = candidate
        elif keep:
            bars.append(candidate)
    if (
        forced_bar is None
        and force_index is not None
        and force_index > last_index
        and force_index < len(MINUTES)
    ):
        # steps ran out before reaching force_index: extend flat from the last price.
        flat = cents(price)
        forced_bar = Bar(MINUTES[force_index], flat, flat, flat, flat, Decimal(100))
    if forced_bar is not None:
        bars.append(forced_bar)
        bars.sort(key=lambda b: b.ts)
    return bars


def _draw_config(draw, zero_costs: bool) -> FillConfig:
    if zero_costs:
        return FillConfig(stop_slippage_bps=Decimal(0))
    return FillConfig(
        entry_slippage_bps=Decimal(draw(st.integers(0, 5))),
        stop_slippage_bps=Decimal(draw(st.integers(0, 10))),
        commission_per_execution=cents(draw(st.integers(0, 100))),
    )


BIAS_GOALS = ("target1", "target2", "stop", "breakeven", "flat")


@st.composite
def biased_bar_paths(draw, signal, start_idx, end_idx):
    """Every expected minute of the window, opening inside the entry zone on the first bar
    (so the order fills there) and drifting through waypoints toward a drawn exit goal."""
    zone_low, zone_high = int(signal.entry_zone_low * 100), int(signal.entry_zone_high * 100)
    stop, target1 = int(signal.stop * 100), int(signal.target1 * 100)
    target2 = None if signal.target2 is None else int(signal.target2 * 100)
    price0 = draw(st.integers(zone_low, zone_high))
    goal = draw(st.sampled_from(BIAS_GOALS))
    if goal == "target1":
        waypoints = [target1 + 5]
    elif goal == "target2":
        waypoints = [(target1 if target2 is None else target2) + 5]
    elif goal == "stop":
        waypoints = [stop - 5]
    elif goal == "breakeven":
        # Touch target1 (stop moves to avg_entry), then fall back below the entry price.
        waypoints = [target1 + 5, price0 - 25]
    else:
        waypoints = [price0]
    count = end_idx - start_idx + 1
    segment_max = max(1, (count - 1) // len(waypoints))
    anchors = [(0, price0)]
    for waypoint in waypoints:
        anchors.append((anchors[-1][0] + draw(st.integers(1, segment_max)), waypoint))
    noise = draw(
        st.lists(
            st.tuples(st.integers(-8, 8), st.integers(0, 12), st.integers(0, 12)),
            min_size=count,
            max_size=count,
        )
    )

    def path(i: int) -> int:
        for (i0, p0), (i1, p1) in zip(anchors, anchors[1:]):
            if i <= i1:
                return p0 + (p1 - p0) * (i - i0) // (i1 - i0)
        return anchors[-1][1]

    bars = []
    previous_close = price0
    for i, (jitter, up, down) in enumerate(noise):
        open_ = previous_close
        close = path(i) + jitter
        high = max(open_, close) + up
        low = min(open_, close) - down
        previous_close = close
        bars.append(
            Bar(MINUTES[start_idx + i], cents(open_), cents(high), cents(low), cents(close), Decimal(100))
        )
    return bars


@st.composite
def scenarios(draw, zero_costs: bool = False, after_fill_bias: bool = False):
    if after_fill_bias:
        # One-session order created shortly before a session close: the whole window fits in
        # one path, the first bar fills, and exits (including TIME_EXIT) are reached often.
        signal = replace(draw(long_signals()), trigger_price=None, valid_sessions=1)
        end_idx = draw(st.sampled_from(FIRST_THREE_SESSION_LAST_INDEXES))
        start_idx = end_idx - draw(st.integers(10, 120))
        created = MINUTES[start_idx] - timedelta(seconds=draw(st.integers(0, 59)))
        begin = evaluation_start_ts(CAL, created)
        valid_until = signal_valid_until_ts(CAL, begin, signal.valid_sessions)
        assert begin == MINUTES[start_idx]
        assert MINUTE_INDEX[CAL.last_expected_minute_before(valid_until)] == end_idx
        config = _draw_config(draw, zero_costs)
        ctx = OrderContext(signal, config, CAL, begin, valid_until)
        return ctx, draw(biased_bar_paths(signal, start_idx, end_idx))

    signal = draw(long_signals())
    created = MINUTES[draw(st.integers(0, FIRST_THREE_SESSIONS - 1))] + timedelta(
        seconds=draw(st.integers(0, 59))
    )
    begin = evaluation_start_ts(CAL, created)
    valid_until = signal_valid_until_ts(CAL, begin, signal.valid_sessions)
    # Anchor the bar path to the order's own evaluation window instead of drawing a
    # start index uniformly over the whole calendar: otherwise most generated paths
    # never intersect [evaluation_start_ts, valid_until_ts) and step() silently drops
    # every bar, so the property passes vacuously on an empty event list.
    start_idx = MINUTE_INDEX[begin]
    end_idx = MINUTE_INDEX[CAL.last_expected_minute_before(valid_until)]
    if draw(st.sampled_from(["start", "end"])) == "start":
        # Some bars precede the window (exercises the pre-window-is-ignored guard), and
        # the window's first expected minute is forced present (see bar_paths) so a
        # short/unlucky "keep" draw can't leave the whole path outside the window.
        first_index = max(0, start_idx - draw(st.integers(0, 20)))
        force_index = start_idx
    else:
        # The path is anchored so it reaches the window's last expected minute, and
        # that exact bar is forced present (see bar_paths), so TIME_EXIT/EXPIRED
        # (only fired on that bar) are actually reachable, not merely likely. Kept
        # short so an open position plausibly survives to expiry without stopping
        # or targeting out first.
        first_index = max(0, end_idx - draw(st.integers(0, 40)))
        force_index = end_idx
    zone_low_cents = int(signal.entry_zone_low * 100)
    config = _draw_config(draw, zero_costs)
    ctx = OrderContext(signal, config, CAL, begin, valid_until)
    return ctx, draw(bar_paths(first_index, zone_low_cents, force_index))


def run(ctx, bars):
    return run_bars(new_order_state(ctx).state, bars, ctx)


def _mirror_price(value):
    return None if value is None else MIRROR_K - value


def mirrored_short(ctx, bars):
    """The SHORT twin of a LONG scenario, reflected around MIRROR_K."""
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
    return short_ctx, short_bars


# --- property bodies (run on both the default and the after-fill-biased strategy) ---


def check_determinism(ctx, bars):
    first, second = run(ctx, bars), run(ctx, bars)
    assert first.events == second.events
    assert [e.payload_hash for e in first.events] == [e.payload_hash for e in second.events]
    assert first.state == second.state


def check_prefix(ctx, bars, cut):
    full = run(ctx, bars).events
    prefix = run(ctx, bars[:cut]).events
    assert full[: len(prefix)] == prefix


def check_accounting(ctx, bars):
    state = new_order_state(ctx).state
    events = []
    for current in bars:
        result = step(state, current, ctx)
        state = result.state
        events.extend(result.events)
        assert state.qty_open >= 0
        assert state.qty_open <= state.qty_total
    assert sum((e.payload.get("pnl", Decimal(0)) for e in events), Decimal(0)) == state.realized_pnl
    costs = sum((e.payload.get("cost", Decimal(0)) for e in events), Decimal(0))
    assert costs == state.costs
    if state.status is OrderStatus.CLOSED:
        assert state.qty_open == 0

    # Independent recomputation of realized P&L from fill prices and quantities.
    fills = [e for e in events if e.type is EventType.FILLED]
    exits = [e for e in events if e.type in EXIT_EVENT_TYPES]
    if not fills:
        assert not exits
        assert state.realized_pnl == 0 and state.costs == 0
        return
    (fill,) = fills
    avg_entry = fill.price
    assert state.avg_entry == avg_entry and state.qty_total == fill.qty
    is_long = ctx.signal.direction is Direction.LONG
    with localcontext(V1_CONTEXT):
        pnl = Decimal(0)
        for exit_event in exits:
            assert exit_event.bar_ts >= fill.bar_ts and exit_event.qty > 0
            edge = exit_event.price - avg_entry if is_long else avg_entry - exit_event.price
            pnl += edge * exit_event.qty
        assert pnl - costs == state.realized_pnl - state.costs
    if exits and (exits[-1].type is not EventType.TARGET1_HIT or exits[-1].payload["final"]):
        assert state.status is OrderStatus.CLOSED


def check_accounting_long_and_short(ctx, bars):
    check_accounting(ctx, bars)
    check_accounting(*mirrored_short(ctx, bars))


def check_short_mirror(ctx, bars):
    short_ctx, short_bars = mirrored_short(ctx, bars)
    long_result = run(ctx, bars)
    short_result = run(short_ctx, short_bars)
    assert [e.type for e in long_result.events] == [e.type for e in short_result.events]
    assert [e.bar_ts for e in long_result.events] == [e.bar_ts for e in short_result.events]
    for long_event, short_event in zip(long_result.events, short_result.events):
        assert short_event.price == _mirror_price(long_event.price)
        assert short_event.qty == long_event.qty
        assert set(short_event.payload) == set(long_event.payload)
        for key, long_value in long_event.payload.items():
            if key.endswith(("_price", "_level")):
                assert short_event.payload[key] == _mirror_price(long_value), key
            else:
                assert short_event.payload[key] == long_value, key
    risk = ctx.config.risk_amount
    assert r_multiple(long_result.state, risk) == r_multiple(short_result.state, risk)


@PROPERTY_SETTINGS
@given(scenarios())
def test_deterministic_events_and_hashes(scenario):
    check_determinism(*scenario)


@PROPERTY_SETTINGS
@given(scenarios(after_fill_bias=True))
def test_deterministic_events_and_hashes_after_fill_bias(scenario):
    check_determinism(*scenario)


@PROPERTY_SETTINGS
@given(scenarios(), st.data())
def test_prefix_no_look_ahead(scenario, data):
    ctx, bars = scenario
    check_prefix(ctx, bars, data.draw(st.integers(0, len(bars))))


@PROPERTY_SETTINGS
@given(scenarios(after_fill_bias=True), st.data())
def test_prefix_no_look_ahead_after_fill_bias(scenario, data):
    ctx, bars = scenario
    check_prefix(ctx, bars, data.draw(st.integers(0, len(bars))))


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
    check_accounting_long_and_short(*scenario)


@PROPERTY_SETTINGS
@given(scenarios(after_fill_bias=True))
def test_accounting_invariants_after_fill_bias(scenario):
    check_accounting_long_and_short(*scenario)


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


# zero_costs=True is required: slippage is computed off abs(price), which is not
# translation-invariant under the K-price mirror, so mirroring is only exact cost-free.
@PROPERTY_SETTINGS
@given(scenarios(zero_costs=True))
def test_short_mirror_matches_long(scenario):
    check_short_mirror(*scenario)


@PROPERTY_SETTINGS
@given(scenarios(zero_costs=True, after_fill_bias=True))
def test_short_mirror_matches_long_after_fill_bias(scenario):
    check_short_mirror(*scenario)


REACHABLE_EVENT_TYPES = (
    EventType.FILLED,
    EventType.ZONE_LOST,
    EventType.ZONE_RECLAIMED,
    EventType.TRIGGER_HIT,
    EventType.TARGET1_HIT,
    EventType.TARGET2_HIT,
    EventType.STOPPED,
    EventType.TIME_EXIT,
    EventType.EXPIRED,
    EventType.INVALIDATED,
)

# TARGET2_HIT requires two sequential touches (target1 then target2) within one path,
# which is rarer than the other single-touch behaviors, so it gets a larger search budget.
HARDER_REACHABILITY_SETTINGS = settings(
    max_examples=1200,
    deadline=None,
    database=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
)
EVENT_REACHABILITY_SETTINGS = {EventType.TARGET2_HIT: HARDER_REACHABILITY_SETTINGS}


def _event_types(scenario) -> set:
    ctx, bars = scenario
    return {event.type for event in run(ctx, bars).events}


def _find_scenario(predicate, description, search_settings=REACHABILITY_SETTINGS) -> None:
    try:
        find(scenarios(), predicate, settings=search_settings)
    except NoSuchExample:
        raise AssertionError(f"strategy never produces: {description}")


def test_strategy_reaches_all_behaviors():
    for event_type in REACHABLE_EVENT_TYPES:
        search_settings = EVENT_REACHABILITY_SETTINGS.get(event_type, REACHABILITY_SETTINGS)
        _find_scenario(lambda s, t=event_type: t in _event_types(s), event_type, search_settings)
    _find_scenario(
        lambda s: any(bar.ts < s[0].evaluation_start_ts for bar in s[1]),
        "a bar before evaluation_start_ts",
    )


BIAS_LABELS = (
    "FILLED", "TARGET1_HIT", "TARGET2_HIT", "STOPPED_INITIAL", "STOPPED_BREAKEVEN", "TIME_EXIT",
)
BIAS_SAMPLE_SIZE = 300
BIAS_MIN_FREQUENCY = Decimal("0.05")


def exit_labels(events) -> set[str]:
    labels = set()
    for event in events:
        if event.type is EventType.STOPPED:
            labels.add(f"STOPPED_{event.payload['stop_kind']}")
        elif event.type.value in BIAS_LABELS:
            labels.add(event.type.value)
    return labels


def measure_after_fill_bias_frequencies(sample_size: int = BIAS_SAMPLE_SIZE) -> tuple[Counter, int]:
    counts: Counter = Counter()
    total = 0

    @settings(
        max_examples=sample_size,
        deadline=None,
        database=None,
        derandomize=True,
        phases=[Phase.generate],
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    )
    @given(scenarios(after_fill_bias=True))
    def sample(scenario):
        nonlocal total
        total += 1
        counts.update(exit_labels(run(*scenario).events))

    sample()
    return counts, total


def test_after_fill_bias_strategy_covers_every_exit_type():
    counts, total = measure_after_fill_bias_frequencies()
    assert total >= BIAS_SAMPLE_SIZE
    for label in BIAS_LABELS:
        assert Decimal(counts[label]) / total >= BIAS_MIN_FREQUENCY, (label, counts[label], total)
