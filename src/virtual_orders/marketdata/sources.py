"""Provider-neutral market-data contracts. Adapters do network I/O; everything returned is Decimal and UTC.

Small capability interfaces: a provider implements only what it offers. Provider payloads never cross
these types; `provider`, `provider_version` and `source` are free text so new providers need no migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from core.domain.models import Bar


class DataTier(StrEnum):
    RESEARCH = "RESEARCH"
    PRODUCTION = "PRODUCTION"


class SourceUnavailable(Exception):
    """Provider unreachable, rate limited or refusing the request."""


class SourceDataError(Exception):
    """Provider answered with data that cannot be trusted (malformed, inconsistent)."""


@dataclass(frozen=True)
class RawBar:
    ticker: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def to_bar(self, batch_id: UUID | None = None) -> Bar:
        return Bar(ts=self.ts, open=self.open, high=self.high, low=self.low, close=self.close,
                   volume=self.volume, batch_id=batch_id)


class BarSource(Protocol):
    provider: str
    provider_version: str
    data_tier: DataTier
    source: str

    def fetch_bars(self, ticker: str, start: datetime, end: datetime) -> list[RawBar]: ...


@dataclass(frozen=True)
class DividendRecord:
    ticker: str
    ex_date: date
    amount: Decimal
    pay_date: date | None


class DividendSource(Protocol):
    name: str

    def fetch_dividends(self, ticker: str, start: date, end: date) -> list[DividendRecord]: ...


@dataclass(frozen=True)
class SplitRecord:
    ticker: str
    ex_date: date
    old_rate: Decimal
    new_rate: Decimal


class SplitSource(Protocol):
    def fetch_splits(self, tickers: Sequence[str], start: date, end: date) -> list[SplitRecord]: ...


class ReferenceSource(Protocol):
    def fetch_minute_bars(self, ticker: str, day: date) -> dict[datetime, Bar]: ...

    def fetch_daily_range(self, ticker: str, day: date) -> tuple[Decimal, Decimal] | None: ...


@dataclass(frozen=True)
class TickerStatus:
    ticker: str
    tradable: bool
    reason: str | None = None


class TickerCheck(Protocol):
    """Spec 3.8: ticker active and tradable. Unavailability raises SourceUnavailable/SourceDataError."""

    name: str

    def check_ticker(self, ticker: str) -> TickerStatus: ...
