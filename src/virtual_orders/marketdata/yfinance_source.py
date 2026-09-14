"""yfinance as cross-check reference (spec 4.6) and second dividend source (spec 4.7)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pandas as pd

from core.domain.models import Bar
from virtual_orders.marketdata.sources import DividendRecord, SourceDataError, SourceUnavailable

HistoryFn = Callable[[str, date, date, str], pd.DataFrame]
PRICE_QUANTUM = Decimal("0.0001")
DIVIDEND_QUANTUM = Decimal("0.000001")
_PRICE_COLUMNS = ("Open", "High", "Low", "Close")


def download_history(ticker: str, start: date, end: date, interval: str) -> pd.DataFrame:
    import yfinance as yf

    frame = yf.Ticker(ticker).history(
        start=start.isoformat(), end=end.isoformat(), interval=interval,
        auto_adjust=False, actions=True, prepost=False, raise_errors=True,
    )
    return cast(pd.DataFrame, frame)


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _quantize(value: Any, quantum: Decimal) -> Decimal:
    if _is_missing(value):
        raise SourceDataError("missing numeric value")
    return Decimal(repr(float(value))).quantize(quantum)


class YFinanceSource:
    name = "yfinance"

    def __init__(self, history: HistoryFn = download_history) -> None:
        self._history_fn = history

    def _history(self, ticker: str, start: date, end: date, interval: str) -> pd.DataFrame:
        try:
            return self._history_fn(ticker, start, end, interval)
        except SourceDataError:
            raise
        except Exception as exc:  # yfinance raises many unrelated exception types
            raise SourceUnavailable(f"yfinance {ticker} {interval}: {exc}") from exc

    def fetch_minute_bars(self, ticker: str, day: date) -> dict[datetime, Bar]:
        frame = self._history(ticker, day, day + timedelta(days=1), "1m")
        bars: dict[datetime, Bar] = {}
        for index, row in frame.iterrows():
            ts = cast(pd.Timestamp, index).to_pydatetime().astimezone(UTC)
            if ts.second or ts.microsecond:
                continue
            if any(_is_missing(row[column]) for column in (*_PRICE_COLUMNS, "Volume")):
                continue
            bars[ts] = Bar(
                ts=ts,
                open=_quantize(row["Open"], PRICE_QUANTUM),
                high=_quantize(row["High"], PRICE_QUANTUM),
                low=_quantize(row["Low"], PRICE_QUANTUM),
                close=_quantize(row["Close"], PRICE_QUANTUM),
                volume=Decimal(int(row["Volume"])),
            )
        return bars

    def fetch_daily_range(self, ticker: str, day: date) -> tuple[Decimal, Decimal] | None:
        frame = self._history(ticker, day, day + timedelta(days=1), "1d")
        for index, row in frame.iterrows():
            if cast(pd.Timestamp, index).date() == day:
                if _is_missing(row["High"]) or _is_missing(row["Low"]):
                    return None
                return _quantize(row["High"], PRICE_QUANTUM), _quantize(row["Low"], PRICE_QUANTUM)
        return None

    def fetch_dividends(self, ticker: str, start: date, end: date) -> list[DividendRecord]:
        frame = self._history(ticker, start, end + timedelta(days=1), "1d")
        records: list[DividendRecord] = []
        for index, row in frame.iterrows():
            value = row["Dividends"]
            if _is_missing(value) or float(value) <= 0:
                continue
            ex_date = cast(pd.Timestamp, index).date()
            if start <= ex_date <= end:
                records.append(DividendRecord(ticker, ex_date, _quantize(value, DIVIDEND_QUANTUM), None))
        return records
