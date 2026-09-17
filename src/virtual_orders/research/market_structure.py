"""Deterministic market context: trend, swings, support/resistance, breakout, gaps (Plan 5, D77).

**The rule that matters here:** every value is computed from candles at or before the analysis index. A swing
point is only usable once it is *confirmed*, which happens `SWING_RIGHT` candles after the pivot itself — so a
pivot that a human would draw on a finished chart is invisible to the engine until the market had actually
produced the candles that confirm it. Classifying a historical signal with a pivot confirmed after it is the
classic look-ahead bug in candlestick research, and `tests/research/test_leakage.py` pins its absence.

Pure: no I/O, no platform imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum

from core.domain.hashing import CANONICAL_CONTEXT
from virtual_orders.research.indicators import IndicatorSeries, distance_pct, slope_at
from virtual_orders.research.models import (
    ONE,
    ZERO,
    Candle,
    PriorTrend,
    TrendState,
    quantize_ratio,
    quantized,
)

CONTEXT_VERSION = "context-v1"
PCT_QUANTUM = Decimal("0.0001")
HUNDRED = Decimal(100)

SWING_LEFT = 2
SWING_RIGHT = 2  # a pivot is confirmed only this many candles later: never read before it exists
PRIOR_TREND_LOOKBACK = 20
TREND_SIDEWAYS_PCT = Decimal("0.5")  # |move| below this over the lookback is sideways
TREND_STRONG_PCT = Decimal("2.0")  # |move| at or above this is full strength
EMA_SLOPE_SPAN = 5
GAP_MIN_PCT = Decimal("0.5")
SHORT_TREND_EMAS = (9, 20)
MEDIUM_TREND_EMAS = (50, 200)


class SwingKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class BreakoutState(StrEnum):
    ABOVE_RESISTANCE = "ABOVE_RESISTANCE"
    BELOW_SUPPORT = "BELOW_SUPPORT"
    INSIDE = "INSIDE"
    UNKNOWN = "UNKNOWN"


class GapState(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SwingPoint:
    kind: SwingKind
    index: int
    ts: datetime
    price: Decimal
    confirmed_index: int

    def usable_at(self, index: int) -> bool:
        """A pivot may only be read once the candles that confirm it have closed."""
        return self.confirmed_index <= index


def swing_points(
    candles: Sequence[Candle], *, left: int = SWING_LEFT, right: int = SWING_RIGHT
) -> list[SwingPoint]:
    """Strict pivots: a high above its `left` neighbours before and `right` after (lows are the mirror).

    Strict comparisons on purpose: a plateau of equal highs is not a pivot, so the same series never yields two
    competing swings at the same price.
    """
    if left < 1 or right < 1:
        raise ValueError("left and right must be >= 1")
    found: list[SwingPoint] = []
    for index in range(left, len(candles) - right):
        candle = candles[index]
        window = list(range(index - left, index)) + list(range(index + 1, index + right + 1))
        if all(candle.high > candles[other].high for other in window):
            found.append(SwingPoint(SwingKind.HIGH, index, candle.ts, candle.high, index + right))
        if all(candle.low < candles[other].low for other in window):
            found.append(SwingPoint(SwingKind.LOW, index, candle.ts, candle.low, index + right))
    return found


def confirmed_swings(swings: Sequence[SwingPoint], index: int) -> list[SwingPoint]:
    return [point for point in swings if point.usable_at(index)]


def _trend_from_move(change_pct: Decimal) -> tuple[TrendState, Decimal]:
    magnitude = abs(change_pct)
    with localcontext(CANONICAL_CONTEXT):
        strength = quantize_ratio(min(magnitude / TREND_STRONG_PCT, ONE))
    if magnitude < TREND_SIDEWAYS_PCT:
        return TrendState.SIDEWAYS, strength
    return (TrendState.UP if change_pct > ZERO else TrendState.DOWN), strength


def prior_trend(
    candles: Sequence[Candle], before_index: int, *, lookback: int = PRIOR_TREND_LOOKBACK
) -> PriorTrend:
    """The move over the `lookback` candles ending strictly **before** `before_index`.

    `before_index` is the index of a pattern's first candle: the trend that names a Hammer must not include the
    Hammer itself. Without a full lookback the trend is reported as unavailable rather than guessed from a
    shorter window, so a pattern that needs context is simply not detected near the start of a series.
    """
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    start = before_index - lookback
    if start < 0 or before_index > len(candles):
        return PriorTrend(TrendState.UNKNOWN, ZERO, lookback, False)
    window = candles[start:before_index]
    first, last = window[0].close, window[-1].close
    if first == ZERO:
        return PriorTrend(TrendState.UNKNOWN, ZERO, lookback, False)
    with localcontext(CANONICAL_CONTEXT):
        change_pct = quantized((last - first) / first * HUNDRED, PCT_QUANTUM)
    state, strength = _trend_from_move(change_pct)
    return PriorTrend(state, strength, lookback, True)


def _ema_trend(series: IndicatorSeries, index: int, periods: tuple[int, int]) -> TrendState:
    fast, slow = (series.ema_at(period, index) for period in periods)
    if fast is None or slow is None:
        return TrendState.UNKNOWN
    if fast > slow:
        return TrendState.UP
    if fast < slow:
        return TrendState.DOWN
    return TrendState.SIDEWAYS


@dataclass(frozen=True)
class MarketStructure:
    context_version: str
    short_term_trend: TrendState
    medium_term_trend: TrendState
    ema20_slope_pct: Decimal | None
    support: Decimal | None
    resistance: Decimal | None
    support_distance_pct: Decimal | None
    resistance_distance_pct: Decimal | None
    breakout: BreakoutState
    relative_atr_pct: Decimal | None
    gap: GapState
    gap_pct: Decimal | None
    swings_confirmed: int
    swing_left: int
    swing_right: int


def _gap(candles: Sequence[Candle], index: int) -> tuple[GapState, Decimal | None]:
    """Open against the previous close, in percent. Only the previous candle of the same series is read."""
    if index < 1:
        return GapState.UNKNOWN, None
    previous, current = candles[index - 1], candles[index]
    change = distance_pct(current.open, previous.close)
    if change is None:
        return GapState.UNKNOWN, None
    if change >= GAP_MIN_PCT:
        return GapState.UP, change
    if change <= -GAP_MIN_PCT:
        return GapState.DOWN, change
    return GapState.NONE, change


def structure_at(
    candles: Sequence[Candle],
    index: int,
    series: IndicatorSeries,
    *,
    swings: Sequence[SwingPoint] | None = None,
    left: int = SWING_LEFT,
    right: int = SWING_RIGHT,
) -> MarketStructure:
    """The market context of `candles[index]`, read only from candles at or before it.

    `swings` may be precomputed for the whole series (a scan computes them once); only the pivots already
    confirmed at `index` are ever consulted, so passing the full list can never leak a later pivot.
    """
    if not 0 <= index < len(candles):
        raise IndexError("index outside the candle series")
    points = swing_points(candles, left=left, right=right) if swings is None else swings
    usable = [point for point in confirmed_swings(points, index) if point.index < index]
    close = candles[index].close
    highs = [point.price for point in usable if point.kind is SwingKind.HIGH]
    lows = [point.price for point in usable if point.kind is SwingKind.LOW]
    above = [price for price in highs if price > close]
    below = [price for price in lows if price < close]
    resistance = min(above) if above else None
    support = max(below) if below else None

    last_high = next((point.price for point in reversed(usable) if point.kind is SwingKind.HIGH), None)
    last_low = next((point.price for point in reversed(usable) if point.kind is SwingKind.LOW), None)
    if last_high is None or last_low is None:
        breakout = BreakoutState.UNKNOWN
    elif close > last_high:
        breakout = BreakoutState.ABOVE_RESISTANCE
    elif close < last_low:
        breakout = BreakoutState.BELOW_SUPPORT
    else:
        breakout = BreakoutState.INSIDE

    atr_value = series.atr_at(index)
    relative_atr = None if atr_value is None or close == ZERO else quantized(
        atr_value / close * HUNDRED, PCT_QUANTUM
    )
    gap_state, gap_change = _gap(candles, index)
    return MarketStructure(
        context_version=CONTEXT_VERSION,
        short_term_trend=_ema_trend(series, index, SHORT_TREND_EMAS),
        medium_term_trend=_ema_trend(series, index, MEDIUM_TREND_EMAS),
        ema20_slope_pct=slope_at(series.ema[20], index, EMA_SLOPE_SPAN) if 20 in series.ema else None,
        support=support,
        resistance=resistance,
        support_distance_pct=distance_pct(close, support),
        resistance_distance_pct=distance_pct(close, resistance),
        breakout=breakout,
        relative_atr_pct=relative_atr,
        gap=gap_state,
        gap_pct=gap_change,
        swings_confirmed=len(usable),
        swing_left=left,
        swing_right=right,
    )
