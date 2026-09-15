from datetime import date
from decimal import Decimal

from sqlalchemy import select

from core.domain.models import FillConfig
from tests.integration.observation_support import window_for
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    TICKER,
    FakeBarSource,
    FakeReference,
    backdated_batch,
    feeds,
    flat_raw,
    scenario_bars,
    signal_body,
    submit_default,
)
from tests.support import et
from virtual_orders.analytics.observation import Alignment, LatencyStats, PressureBucket, Strength, whole_seconds
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD
from virtual_orders.evaluator.commands import flag_order_review
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.quality import run_end_of_day
from virtual_orders.evaluator.signals import submit_signal
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation_trades import PressureBefore, pressure_before, trade_sections
from virtual_orders.storage import tables

SESSION = date(2025, 11, 25)
NEXT = "2025-11-26"


def projection(engine, order_id):
    with engine.connect() as conn:
        return conn.execute(select(tables.order_state).where(tables.order_state.c.order_id == order_id)).one()


def test_trades_latency_and_unfilled_orders_of_a_session(engine):
    source = FakeBarSource(scenario_bars() + flat_raw(DAY, "09:30", "16:00", 105, "MSFT"))
    kept = submit_default(engine).auto_order_id
    reviewed = submit_default(engine, client_signal_id="rex-2").auto_order_id
    submit_default(engine, client_signal_id="msft-1", ticker="MSFT", valid_sessions=1)  # never fills: expires
    submit_default(engine, client_signal_id="msft-3", ticker="MSFT")  # never fills: still waiting
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(DAY, hm))
    run_end_of_day(engine, gateway=feeds(source), reference=FakeReference(), session_day=SESSION,
                   code_version=CODE_VERSION, market_now=et(DAY, "16:30"))
    flag_order_review(engine, reviewed, reason="MANUAL", ref=DAY)

    with engine.connect() as conn:
        facts = trade_sections(conn, window_for(engine, SESSION))

    trades, latency, pressure = facts.trades, facts.latency, facts.pressure
    assert (trades.created, trades.filled, trades.closed, trades.excluded_needs_review, trades.replay_closed) == (
        {"AUTO_STRATEGY": 4}, 2, 2, 1, 0)
    assert [(row.order_id, row.needs_review) for row in trades.rows] == sorted(
        [(kept, False), (reviewed, True)], key=lambda item: str(item[0]))
    state = projection(engine, kept)
    row = next(item for item in trades.rows if item.order_id == kept)
    assert (row.ticker, row.direction, row.origin, row.closed_at) == (TICKER, "LONG", "AUTO_STRATEGY", et(DAY, "12:50"))
    assert (row.r_multiple, row.mfe_r, row.mae_r) == (state.r_multiple, state.mfe_r, state.mae_r)
    assert (trades.stats.trades, trades.stats.wins, trades.stats.sum_r) == (1, 1, Decimal("1.7500"))
    assert trades.stats.mean_mfe_r == state.mfe_r.quantize(Decimal("0.0001"))
    assert list(trades.stats_by_origin) == ["AUTO_STRATEGY"]

    assert latency.bar == LatencyStats(count=2, median_seconds=3900, p90_seconds=3900, max_seconds=3900)
    assert latency.bar_by_origin == {"AUTO_STRATEGY": latency.bar}
    assert latency.unfilled == {"EXPIRED": 1, "NOT_FILLED_YET": 1}
    fill = latency.rows[0]
    assert (fill.signal_created_at, fill.fill_bar_ts, fill.bar_latency_seconds) == (
        et(DAY, "09:00"), et(DAY, "10:05"), 3900)
    assert fill.recorded_latency_seconds == whole_seconds(fill.signal_created_at, fill.recorded_at)
    assert latency.recorded.count == 2

    assert (pressure.estimate, pressure.method, pressure.disclaimer) == (True, METHOD, DISCLAIMER)
    assert pressure.buckets == (  # one trade: no mean below MIN_TRADES_FOR_BUCKET_MEAN
        PressureBucket(Alignment.UNAVAILABLE, None, 1, 1, Decimal("1.7500"), None),)
    assert pressure.min_trades_for_mean == 5
    assert pressure.unavailable_reasons == {"NO_BARS": 1}  # the 09:00 signal had no stored bar before it
    assert row.pressure_unavailable_reason == "NO_BARS" and row.pressure_cmf is None
    assert (row.pressure_window_start, row.pressure_window_end, row.pressure_spans_sessions) == (None, None, None)


def test_pressure_is_recomputed_from_bars_before_the_signal_and_paired_with_the_result(engine):
    backdated_batch(engine, TICKER, scenario_bars(), ingested_at=et(DAY, "13:00"))  # the previous session, 09:30-12:50
    backdated_batch(engine, "MSFT", flat_raw(DAY, "15:50", "16:00", 50, "MSFT"), ingested_at=et(DAY, "16:05"))
    source = FakeBarSource(scenario_bars(NEXT))
    order_id = submit_signal(engine, signal_body(client_signal_id="next-day"), config=FillConfig(),
                             code_version=CODE_VERSION, price_source=PRICE_SOURCE, now=et(NEXT, "09:00")).auto_order_id
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(engine, feeds(source), code_version=CODE_VERSION, market_now=et(NEXT, hm))

    as_of = acquire_data_as_of(engine)
    with engine.connect() as conn:
        facts = trade_sections(conn, window_for(engine, date(2025, 11, 26)))
        insufficient = pressure_before(conn, ticker="MSFT", price_source=PRICE_SOURCE, decided_at=et(NEXT, "09:00"),
                                       as_of=as_of)
        nothing = pressure_before(conn, ticker="NVDA", price_source=PRICE_SOURCE, decided_at=et(NEXT, "09:00"),
                                  as_of=as_of)

    (row,) = facts.trades.rows
    # Last 30 stored bars before 09:00 ET: 12:21-12:50 of the previous session. 29 flat bars and the 12:50 bar
    # (H 110.5, L 108.8, C 110): CMF = 0.4118 * 1000 / 30000 = 0.0137, below 0.05 -> no strong side.
    assert (row.order_id, row.pressure_cmf, row.pressure_alignment, row.pressure_strength) == (
        order_id, Decimal("0.0137"), Alignment.NEUTRAL, Strength.WEAK)
    assert (row.pressure_window_start, row.pressure_window_end, row.pressure_spans_sessions) == (
        et(DAY, "12:21"), et(DAY, "12:50"), False)
    assert facts.pressure.buckets == (
        PressureBucket(Alignment.NEUTRAL, Strength.WEAK, 1, 1, Decimal("1.7500"), None),)
    assert facts.pressure.unavailable_reasons == {}
    # MSFT has only 10 stored bars in its whole history as of the report; NVDA has none.
    assert insufficient == PressureBefore(None, "INSUFFICIENT_BARS", None, None, None)
    assert nothing == PressureBefore(None, "NO_BARS", None, None, None)


def test_an_early_session_signal_reads_the_last_bars_of_the_previous_session(engine):
    backdated_batch(engine, TICKER, scenario_bars(), ingested_at=et(DAY, "13:00"))  # 09:30-12:50 of the previous session
    backdated_batch(engine, TICKER, flat_raw(NEXT, "09:30", "09:45", 107), ingested_at=et(NEXT, "09:45"))  # 15 bars
    as_of = acquire_data_as_of(engine)

    with engine.connect() as conn:
        early = pressure_before(conn, ticker=TICKER, price_source=PRICE_SOURCE, decided_at=et(NEXT, "09:45", 30),
                                as_of=as_of)
        before_open_bars = pressure_before(conn, ticker=TICKER, price_source=PRICE_SOURCE,
                                           decided_at=et(NEXT, "09:45", 30), as_of=et(NEXT, "09:40"))

    # D66: a 09:45 ET signal is never INSUFFICIENT_BARS by construction. The last 30 bars before 09:45 are
    # 12:36-12:50 of the previous session and 09:30-09:44 of this one.
    assert early.unavailable_reason is None and early.estimate is not None and early.estimate.bars == 30
    assert (early.window_start, early.window_end, early.spans_sessions) == (et(DAY, "12:36"), et(NEXT, "09:44"), True)
    assert (early.estimate.first_bar_ts, early.estimate.last_bar_ts) == (early.window_start, early.window_end)
    # As of 09:40 the 09:30-09:44 batch was not ingested yet: only the previous session's bars count.
    assert (before_open_bars.window_start, before_open_bars.window_end, before_open_bars.spans_sessions) == (
        et(DAY, "12:21"), et(DAY, "12:50"), False)
