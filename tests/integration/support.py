"""Shared integration helpers: fake providers and scenario bars. Never touches the network."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select

from core.domain.models import Bar, FillConfig
from tests.support import et
from virtual_orders.evaluator.signals import SignalSubmission, submit_signal
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.sources import DataTier, DividendRecord, RawBar, SourceUnavailable, SplitRecord
from virtual_orders.storage import tables

TICKER = "AAPL"
DAY = "2025-11-25"
PRICE_SOURCE = "fake_feed"  # deliberately not a real provider: the suite proves adapters are replaceable


def _d(value) -> Decimal:
    return Decimal(str(value))


def raw(day: str, hm: str, o, h, l, c, volume=1000, ticker: str = TICKER) -> RawBar:  # noqa: E741
    return RawBar(ticker, et(day, hm), _d(o), _d(h), _d(l), _d(c), _d(volume))


def flat_raw(day: str, start_hm: str, end_hm: str, price, ticker: str = TICKER) -> list[RawBar]:
    start, end = et(day, start_hm), et(day, end_hm)
    minutes = calendar_for_window(start, end).expected_minutes(start, end)
    return [RawBar(ticker, m, _d(price), _d(price), _d(price), _d(price), _d(1000)) for m in minutes]


def feeds(*sources: FakeBarSource) -> MarketDataGateway:
    return MarketDataGateway(sources)


class FakeBarSource:
    provider = "fake"
    provider_version = "test"
    data_tier = DataTier.RESEARCH

    def __init__(self, bars: Iterable[RawBar] = (), source: str = PRICE_SOURCE) -> None:
        self.source = source
        self.bars: dict[tuple[str, datetime], RawBar] = {}
        self.calls: list[tuple[str, datetime, datetime]] = []
        self.failing: set[str] = set()
        self.load(bars)

    def load(self, bars: Iterable[RawBar]) -> None:
        """Adds bars; a bar for an existing minute replaces it (vendor correction)."""
        for item in bars:
            self.bars[(item.ticker, item.ts)] = item

    def remove(self, ticker: str, ts: datetime) -> None:
        self.bars.pop((ticker, ts), None)

    def fetch_bars(self, ticker: str, start: datetime, end: datetime) -> list[RawBar]:
        self.calls.append((ticker, start, end))
        if ticker in self.failing:
            raise SourceUnavailable(f"{ticker} unavailable (fake)")
        return sorted(
            (b for (t, ts), b in self.bars.items() if t == ticker and start <= ts < end),
            key=lambda b: b.ts,
        )


def backdated_batch(
    engine: Engine,
    ticker: str,
    bars: Iterable[RawBar],
    ingested_at: datetime,
    batch_id: UUID | None = None,
    source: str = PRICE_SOURCE,
) -> UUID:
    """Test-only: writes a batch with an explicit ingested_at, bypassing the watermark lock."""
    batch_id = batch_id or uuid4()
    with engine.begin() as conn:
        conn.execute(tables.bar_batches.insert().values(
            batch_id=batch_id, provider="fake", provider_version="backdated", data_tier="RESEARCH",
            request={"ticker": ticker}, content_hash="backdated", ingested_at=ingested_at,
        ))
        conn.execute(tables.bars_1m.insert(), [
            dict(ticker=ticker, ts=b.ts, open=b.open, high=b.high, low=b.low, close=b.close,
                 volume=b.volume, source=source, batch_id=batch_id)
            for b in bars
        ])
    return batch_id


def count(engine: Engine, table_name: str) -> int:
    table = tables.metadata.tables[table_name]
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


CODE_VERSION = "test-sha"
SIGNAL_CREATED_AT = et(DAY, "09:00")


def signal_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "client_signal_id": "rex-2025-11-25-aapl",
        "strategy": "REXSHARE",
        "strategy_version": "1.0",
        "source": "test",
        "ticker": TICKER,
        "direction": "LONG",
        "entry_zone_low": Decimal("100"),
        "entry_zone_high": Decimal("102"),
        "stop": Decimal("97"),
        "target1": Decimal("106"),
        "target2": Decimal("110"),
        "valid_sessions": 3,
    }
    body.update(overrides)
    return body


def scenario_bars(day: str = DAY, ticker: str = TICKER) -> list[RawBar]:
    """201 bars 09:30-12:50: fill 10:05 @101, TARGET1 11:00 @106, TARGET2 12:50 @110 (r = 1.75)."""
    return (
        flat_raw(day, "09:30", "10:05", 105, ticker)
        + [raw(day, "10:05", 101, 101.5, 100.5, 101.2, ticker=ticker)]
        + flat_raw(day, "10:06", "11:00", 103, ticker)
        + [raw(day, "11:00", 105, 106, 104.8, 105.5, ticker=ticker)]
        + flat_raw(day, "11:01", "12:50", 107, ticker)
        + [raw(day, "12:50", 109, 110.5, 108.8, 110, ticker=ticker)]
    )


def submit_default(engine: Engine, **overrides: Any) -> SignalSubmission:
    return submit_signal(engine, signal_body(**overrides), config=FillConfig(),
                         code_version=CODE_VERSION, price_source=PRICE_SOURCE, now=SIGNAL_CREATED_AT)


def dividend(ticker: str, day: str, amount: str) -> DividendRecord:
    return DividendRecord(ticker, date.fromisoformat(day), Decimal(amount), None)


class FakeDividends:
    def __init__(self, name: str, records: Iterable[DividendRecord] = (), failing: bool = False) -> None:
        self.name = name
        self.records = list(records)
        self.failing = failing

    def fetch_dividends(self, ticker: str, start: date, end: date) -> list[DividendRecord]:
        if self.failing:
            raise SourceUnavailable(f"{self.name} unavailable (fake)")
        return [r for r in self.records if r.ticker == ticker and start <= r.ex_date <= end]


class FakeSplits:
    def __init__(self, records: Iterable[SplitRecord] = ()) -> None:
        self.records = list(records)
        self.calls: list[tuple[tuple[str, ...], date, date]] = []

    def fetch_splits(self, tickers: Sequence[str], start: date, end: date) -> list[SplitRecord]:
        self.calls.append((tuple(tickers), start, end))
        return [r for r in self.records if r.ticker in tickers and start <= r.ex_date <= end]


class FakeReference:
    """yfinance stand-in: minute[(ticker, day)] -> {ts: Bar}; daily[(ticker, day)] -> (high, low)."""

    def __init__(
        self,
        minute: dict[tuple[str, date], dict[datetime, Bar]] | None = None,
        daily: dict[tuple[str, date], tuple[Decimal, Decimal]] | None = None,
        failing: bool = False,
    ) -> None:
        self.minute = minute or {}
        self.daily = daily or {}
        self.failing = failing

    def fetch_minute_bars(self, ticker: str, day: date) -> dict[datetime, Bar]:
        if self.failing:
            raise SourceUnavailable("yfinance unavailable (fake)")
        return dict(self.minute.get((ticker, day), {}))

    def fetch_daily_range(self, ticker: str, day: date) -> tuple[Decimal, Decimal] | None:
        if self.failing:
            raise SourceUnavailable("yfinance unavailable (fake)")
        return self.daily.get((ticker, day))
