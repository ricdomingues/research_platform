from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from virtual_orders.marketdata.sources import SourceUnavailable
from virtual_orders.marketdata.yfinance_source import YFinanceSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "yfinance"


def load(name: str) -> pd.DataFrame:
    frame = pd.read_csv(FIXTURES / name)
    stamps = pd.to_datetime(frame.pop(frame.columns[0]), utc=True)
    frame.index = pd.DatetimeIndex(stamps).tz_convert("America/New_York")
    return frame


def recorded_history(calls: list[tuple]):
    minute, daily = load("aapl_1m_2025-11-25.csv"), load("aapl_1d_2025-11.csv")

    def history(ticker: str, start: date, end: date, interval: str) -> pd.DataFrame:
        calls.append((ticker, start, end, interval))
        frame = minute if interval == "1m" else daily
        days = frame.index.date
        return frame[(days >= start) & (days < end)]

    return history


def test_minute_bars_are_utc_decimal_and_skip_empty_rows():
    calls: list[tuple] = []
    bars = YFinanceSource(recorded_history(calls)).fetch_minute_bars("AAPL", date(2025, 11, 25))
    open_utc = datetime(2025, 11, 25, 14, 30, tzinfo=UTC)
    assert sorted(bars) == [open_utc, open_utc.replace(minute=31), open_utc.replace(minute=33)]
    first = bars[open_utc]
    assert first.low == Decimal("101.1000") and first.close == Decimal("101.4000")
    assert first.volume == Decimal(120000) and first.ts == open_utc
    assert calls == [("AAPL", date(2025, 11, 25), date(2025, 11, 26), "1m")]


def test_daily_range_for_session_or_none():
    source = YFinanceSource(recorded_history([]))
    assert source.fetch_daily_range("AAPL", date(2025, 11, 25)) == (Decimal("280.38"), Decimal("275.25"))
    assert source.fetch_daily_range("AAPL", date(2025, 11, 26)) is None


def test_dividends_from_daily_actions():
    source = YFinanceSource(recorded_history([]))
    records = source.fetch_dividends("AAPL", date(2025, 11, 1), date(2025, 11, 30))
    assert [(r.ticker, r.ex_date, r.amount, r.pay_date) for r in records] == [
        ("AAPL", date(2025, 11, 10), Decimal("0.26"), None)
    ]
    assert source.name == "yfinance"


def test_provider_failure_becomes_unavailable():
    def broken(ticker, start, end, interval):
        raise RuntimeError("Too Many Requests")

    with pytest.raises(SourceUnavailable, match="Too Many Requests"):
        YFinanceSource(broken).fetch_minute_bars("AAPL", date(2025, 11, 25))
