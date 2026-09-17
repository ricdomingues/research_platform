"""The RESEARCH_SCAN job over real Postgres: stored bars only, idempotent, and isolated per ticker.

Nothing in this file hands the scan a gateway, a source or a provider of any kind. It reads what ingestion
already wrote, which is the whole point of the job: research can never be the reason a provider is called.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from tests.integration.support import DAY, PRICE_SOURCE, backdated_batch, count
from tests.support import et
from virtual_orders.alerts.watchlist import add_ticker
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.sources import RawBar
from virtual_orders.research.models import Timeframe
from virtual_orders.research.service import (
    NO_WATCHLIST,
    ScanConfig,
    run_research_scan,
    scan_configuration,
)
from virtual_orders.storage import tables

MARKET_NOW = et(DAY, "16:30")
INGESTED_AT = et(DAY, "16:05")
CONFIG = ScanConfig(timeframes=(Timeframe.M15,), lookback_sessions=2)


def session_minutes():
    start, end = et(DAY, "09:30"), et(DAY, "16:00")
    return calendar_for_window(start, end).expected_minutes(start, end)


def marching_bars(ticker="AAPL", start_price=120.0, step=0.05):
    """A session that falls for its first half and rises for its second.

    At 15 minutes this aggregates into real-bodied candles that march and open inside the previous body, which
    is a Three Black Crows and then a Three White Soldiers by the engine's own rule — a deterministic way to
    give the scan something real to find.
    """
    minutes = session_minutes()
    bars, price = [], start_price
    for index, minute in enumerate(minutes):
        move = -step if index < len(minutes) // 2 else step
        close = price + move
        bars.append(RawBar(
            ticker, minute, Decimal(str(round(price, 4))), Decimal(str(round(max(price, close) + 0.01, 4))),
            Decimal(str(round(min(price, close) - 0.01, 4))), Decimal(str(round(close, 4))), Decimal("1000"),
        ))
        price = close
    return bars


def seed(engine, ticker="AAPL", **kwargs):
    backdated_batch(engine, ticker, marching_bars(ticker, **kwargs), ingested_at=INGESTED_AT)
    with engine.begin() as conn:
        add_ticker(conn, ticker, added_at=et(DAY, "09:00"))


def store_unreadable_bar(engine, ticker="MSFT"):
    """A row that violates the bar invariant (high below low), written straight to the table so it bypasses `Bar`.

    Reading it raises inside that ticker's own transaction, which is exactly the failure the scan has to
    contain instead of propagating.
    """
    batch_id = uuid4()
    with engine.begin() as conn:
        conn.execute(tables.bar_batches.insert().values(
            batch_id=batch_id, provider="fake", provider_version="corrupt", data_tier="RESEARCH",
            request={"ticker": ticker}, content_hash="corrupt", ingested_at=INGESTED_AT,
        ))
        conn.execute(tables.bars_1m.insert().values(
            ticker=ticker, ts=et(DAY, "10:00"), open=Decimal("10"), high=Decimal("1"), low=Decimal("50"),
            close=Decimal("10"), volume=Decimal("1000"), source=PRICE_SOURCE, batch_id=batch_id,
        ))
        add_ticker(conn, ticker, added_at=et(DAY, "09:00"))


def test_an_empty_watchlist_is_skipped_without_a_run(engine):
    report = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                               market_now=MARKET_NOW, config=CONFIG)
    assert (report.run_id, report.skipped) == (None, NO_WATCHLIST)
    assert count(engine, "research_runs") == 0


def test_the_scan_reads_stored_bars_and_records_what_it_finds(engine):
    seed(engine)
    report = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                               market_now=MARKET_NOW, config=CONFIG)
    assert report.run_id is not None and report.skipped is None
    assert report.tickers == ("AAPL",)
    assert report.scanned == {"AAPL:15m": 26}  # 390 session minutes / 15
    assert report.failures == {}
    assert report.detections > 0 and report.candidates > 0
    assert count(engine, "pattern_detections") == report.detections
    assert count(engine, "setup_candidates") == report.candidates

    with engine.connect() as conn:
        rows = conn.execute(select(tables.pattern_detections)).mappings().all()
        run = conn.execute(select(tables.research_runs)).mappings().one()
    # One watermark for the whole run: every observation is as-of the same instant.
    assert {row["data_as_of"] for row in rows} == {report.data_as_of}
    assert {row["price_source"] for row in rows} == {PRICE_SOURCE}
    assert {row["timeframe"] for row in rows} == {"15m"}
    assert run["status"] == "COMPLETED" and run["completed_at"] is not None
    assert run["detail"]["candles_scanned"] == {"AAPL:15m": 26}
    assert run["detail"]["configuration"]["engine_version"] == "candles-v1"
    assert run["kind"] == "RESEARCH_SCAN"


def test_running_the_same_scan_again_creates_no_duplicate_observations(engine):
    seed(engine)
    first = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                              market_now=MARKET_NOW, config=CONFIG)
    detections, candidates = count(engine, "pattern_detections"), count(engine, "setup_candidates")
    second = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                               market_now=MARKET_NOW, config=CONFIG)
    assert first.detections > 0
    assert (second.detections, second.candidates) == (0, 0)  # nothing new to record
    assert count(engine, "pattern_detections") == detections
    assert count(engine, "setup_candidates") == candidates
    assert count(engine, "research_runs") == 2  # the run itself is always recorded


def test_one_unreadable_ticker_never_stops_the_others(engine):
    seed(engine)
    store_unreadable_bar(engine)
    report = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                               market_now=MARKET_NOW, config=CONFIG)
    assert report.failures == {"MSFT:15m": "ERROR:ValueError"}
    assert report.scanned["AAPL:15m"] == 26  # the healthy ticker was scanned anyway
    assert report.detections > 0
    with engine.connect() as conn:
        run = conn.execute(select(tables.research_runs)).mappings().one()
    assert run["status"] == "COMPLETED"  # a bad symbol is a recorded failure, not a failed run
    assert run["detail"]["failures"] == {"MSFT:15m": "ERROR:ValueError"}


def test_a_watched_ticker_without_stored_bars_is_visited_and_reported_as_empty(engine):
    with engine.begin() as conn:
        add_ticker(conn, "NVDA", added_at=et(DAY, "09:00"))
    report = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                               market_now=MARKET_NOW, config=CONFIG)
    assert report.run_id is not None
    assert report.failures == {} and report.detections == 0
    # Visited and found empty is recorded as zero candles, which is not the same fact as never looked at.
    assert report.scanned == {"NVDA:15m": 0}


def test_only_completed_candles_are_scanned(engine):
    seed(engine)
    # Mid-session: two 15-minute candles have closed, the one still forming has not.
    report = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                               market_now=et(DAY, "10:07"), config=CONFIG)
    assert report.scanned == {"AAPL:15m": 2}  # 09:30-09:44 and 09:45-09:59 only
    with engine.connect() as conn:
        latest = conn.execute(select(func.max(tables.pattern_detections.c.end_ts))).scalar()
    assert latest is None or latest <= et(DAY, "09:59")


def test_a_later_scan_extends_the_history_without_rewriting_it(engine):
    seed(engine)
    morning = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                                market_now=et(DAY, "12:00"), config=CONFIG)
    after_close = run_research_scan(engine, code_version="test-sha", price_source=PRICE_SOURCE,
                                    market_now=MARKET_NOW, config=CONFIG)
    assert morning.scanned["AAPL:15m"] == 10
    assert after_close.scanned["AAPL:15m"] == 26
    assert after_close.detections > 0  # the afternoon's candles are new observations
    with engine.connect() as conn:
        runs = conn.execute(select(tables.research_runs.c.status)).scalars().all()
    assert list(runs) == ["COMPLETED", "COMPLETED"]


def test_scan_configuration_parses_operator_values():
    assert scan_configuration(None) is not None
    config = scan_configuration({"timeframes": ["5m", "1h"], "patterns": ["HAMMER"], "lookback_sessions": 3})
    assert config.timeframes == (Timeframe.M5, Timeframe.H1)
    assert config.patterns == ("HAMMER",) and config.lookback_sessions == 3
    with pytest.raises(ValueError, match="at least one timeframe"):
        scan_configuration({"timeframes": []})


def test_the_scan_never_needs_a_market_data_gateway():
    """The signature itself is the guarantee: there is nowhere to pass a provider even by accident."""
    import inspect

    parameters = set(inspect.signature(run_research_scan).parameters)
    assert parameters == {"engine", "code_version", "price_source", "market_now", "config", "tickers"}
