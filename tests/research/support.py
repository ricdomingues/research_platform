"""Synthetic candle builders shared by the research tests. Pure: no database, no network, no calendar loading.

Every helper produces `Candle` values directly, so a pattern test states the geometry it means instead of
hiding it behind a resampling step. The timeframe-aggregation tests build their bars from a real NYSE calendar
instead; that is their subject, not their fixture.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from tests.support import et
from virtual_orders.research.models import Candle, Timeframe

SESSION_DAY = date(2025, 11, 25)
BASE = et("2025-11-25", "09:30")
STEP = timedelta(minutes=15)


def D(value: object) -> Decimal:
    return Decimal(str(value))


def candle(
    index: int,
    open_: object,
    high: object,
    low: object,
    close: object,
    volume: object = 1000,
    *,
    timeframe: Timeframe = Timeframe.M15,
    minutes: int = 15,
    present: int | None = None,
    session_day: date = SESSION_DAY,
    base: datetime = BASE,
) -> Candle:
    """One candle at `index` steps after the session open. OHLC is given explicitly, never inferred."""
    ts = base + STEP * index
    return Candle(
        timeframe=timeframe, ts=ts, end_ts=ts + timedelta(minutes=minutes - 1), session_day=session_day,
        open=D(open_), high=D(high), low=D(low), close=D(close), volume=D(volume),
        minutes_expected=minutes, minutes_present=minutes if present is None else present, truncated=False,
    )


def flat(index: int, price: object, volume: object = 1000) -> Candle:
    """A doji-ish candle with a tiny body: useful as filler that no directional rule will match."""
    value = D(price)
    return candle(index, value, value + D("0.2"), value - D("0.2"), value, volume)


def trend(
    start_index: int, first_price: float, count: int, step: float, *, volume: object = 1000
) -> list[Candle]:
    """`count` candles marching by `step`, each body real and each wick small.

    A positive step gives bullish candles that close higher; a negative step gives the mirror. Opens sit inside
    the previous body, so a long enough run is a legitimate Three White Soldiers / Three Black Crows.
    """
    candles: list[Candle] = []
    price = first_price
    for offset in range(count):
        close = price + step
        high, low = max(price, close) + 0.2, min(price, close) - 0.2
        candles.append(candle(start_index + offset, round(price, 2), round(high, 2), round(low, 2),
                              round(close, 2), volume))
        price = close
    return candles


def decline(count: int = 25, *, start: float = 120.0, step: float = -1.0) -> list[Candle]:
    return trend(0, start, count, step)


def bullish_engulfing_after_decline() -> list[Candle]:
    """A planted Bullish Engulfing: a small bearish candle swallowed by the next candle, after a real decline.

    Follow-through candles are appended so the pattern has a future to be labelled against. They march in one
    direction and so never form a second engulfing, which keeps the fixture's single detection unambiguous.
    """
    candles = decline()
    index = len(candles)
    candles.append(candle(index, 100, "100.2", "97.8", 98))  # bearish body of 2
    candles.append(candle(index + 1, "97.5", "100.7", "97.3", "100.5", 3000))  # bullish body of 3, engulfing
    candles.extend(trend(index + 2, 100.5, 12, 0.5))
    return candles


def hammer_candle(index: int) -> Candle:
    """Long lower wick, small body near the top, short upper wick: Hammer or Hanging Man, by context alone."""
    return candle(index, "100.4", "100.6", "99.0", "100.5")


def inverted_hammer_candle(index: int) -> Candle:
    return candle(index, "100.4", "102.0", "100.3", "100.5")


def zigzag(cycles: int = 5, *, period: int = 20, amplitude: float = 1.0, start: float = 100.0) -> list[Candle]:
    """Alternating up and down legs: produces marching patterns and reversals with mixed, realistic outcomes."""
    candles: list[Candle] = []
    price = start
    for cycle in range(cycles):
        step = amplitude * (1.0 + (cycle % 3) * 0.6)
        for direction in (1, -1):
            leg = trend(len(candles), price, period, step * direction,
                        volume=1000 + (cycle % 5) * 400)
            candles.extend(leg)
            price = float(leg[-1].close)
    return candles


def closes(candles: Sequence[Candle]) -> list[Decimal]:
    return [item.close for item in candles]
