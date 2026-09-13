"""Shared integration helpers: fake providers and scenario bars. Never touches the network."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select

from tests.support import et
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.sources import DataTier, RawBar, SourceUnavailable
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
