"""Deterministic technical indicators over an ordered candle series (Plan 5, D76).

Pure and Decimal-only: every division runs in the canonical decimal context, so the same candles always give
the same values, on any machine. Every series returned is **aligned with its input** and padded with `None`
until the indicator has enough history — an indicator never borrows a later candle to fill an earlier slot,
which is what makes the feature snapshots of `features.py` leak-free by construction.

This module deliberately does **not** reimplement VWAP, CMF, OBV or the OHLCV pressure estimate: those already
exist as `virtual_orders.analytics.vwap` and `virtual_orders.analytics.pressure` and are reused as they are,
disclaimer included.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from core.domain.hashing import CANONICAL_CONTEXT
from virtual_orders.research.models import ZERO, Candle, quantize_ratio, quantized

PRICE_QUANTUM = Decimal("0.000001")
PCT_QUANTUM = Decimal("0.0001")
EMA_PERIODS = (9, 20, 50, 200)
RSI_PERIOD = 14
ATR_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
RELATIVE_VOLUME_LOOKBACK = 20
VOLATILITY_LOOKBACK = 20
HUNDRED = Decimal(100)
TWO = Decimal(2)


def closes(candles: Sequence[Candle]) -> list[Decimal]:
    return [candle.close for candle in candles]


def _require_period(period: int) -> None:
    if period < 1:
        raise ValueError("period must be >= 1")


def sma(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Simple moving average, `None` until `period` values exist."""
    _require_period(period)
    result: list[Decimal | None] = [None] * len(values)
    with localcontext(CANONICAL_CONTEXT):
        running = ZERO
        for index, value in enumerate(values):
            running += value
            if index >= period:
                running -= values[index - period]
            if index >= period - 1:
                result[index] = quantized(running / period, PRICE_QUANTUM)
    return result


def ema(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Exponential moving average seeded with the simple average of the first `period` values.

    The seed is stated on purpose: an EMA seeded differently is a different indicator, and a stored feature
    snapshot must stay explainable years later.
    """
    _require_period(period)
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return result
    with localcontext(CANONICAL_CONTEXT):
        multiplier = TWO / (period + 1)
        current = sum(values[:period], ZERO) / period
        result[period - 1] = quantized(current, PRICE_QUANTUM)
        for index in range(period, len(values)):
            current = (values[index] - current) * multiplier + current
            result[index] = quantized(current, PRICE_QUANTUM)
    return result


def _wilder(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Wilder's smoothing: the seed is the simple mean of the first `period` values, then (prev*(n-1)+x)/n."""
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return result
    with localcontext(CANONICAL_CONTEXT):
        current = sum(values[:period], ZERO) / period
        result[period - 1] = current
        for index in range(period, len(values)):
            current = (current * (period - 1) + values[index]) / period
            result[index] = current
    return result


def rsi(values: Sequence[Decimal], period: int = RSI_PERIOD) -> list[Decimal | None]:
    """Wilder's RSI in [0, 100]. A window with no loss at all is 100 by definition, never a division by zero."""
    _require_period(period)
    result: list[Decimal | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    with localcontext(CANONICAL_CONTEXT):
        for earlier, later in zip(values, values[1:], strict=False):
            change = later - earlier
            gains.append(change if change > ZERO else ZERO)
            losses.append(-change if change < ZERO else ZERO)
        average_gain = _wilder(gains, period)
        average_loss = _wilder(losses, period)
        for index in range(len(gains)):
            up, down = average_gain[index], average_loss[index]
            if up is None or down is None:
                continue
            value = HUNDRED if down == ZERO else HUNDRED - HUNDRED / (1 + up / down)
            result[index + 1] = quantized(value, PCT_QUANTUM)  # changes are offset by one candle
    return result


def true_ranges(candles: Sequence[Candle]) -> list[Decimal]:
    """max(high-low, |high-prev_close|, |low-prev_close|); the first candle has only its own range."""
    values: list[Decimal] = []
    with localcontext(CANONICAL_CONTEXT):
        for index, candle in enumerate(candles):
            span = candle.high - candle.low
            if index == 0:
                values.append(span)
                continue
            previous = candles[index - 1].close
            values.append(max(span, abs(candle.high - previous), abs(candle.low - previous)))
    return values


def atr(candles: Sequence[Candle], period: int = ATR_PERIOD) -> list[Decimal | None]:
    _require_period(period)
    smoothed = _wilder(true_ranges(candles), period)
    return [None if value is None else quantized(value, PRICE_QUANTUM) for value in smoothed]


@dataclass(frozen=True)
class Macd:
    macd: list[Decimal | None]
    signal: list[Decimal | None]
    histogram: list[Decimal | None]


def macd(
    values: Sequence[Decimal], fast: int = MACD_FAST, slow: int = MACD_SLOW, signal: int = MACD_SIGNAL
) -> Macd:
    """MACD line, its signal EMA and the histogram, all aligned with `values`.

    The signal EMA is computed over the MACD line only from the candle where the line first exists, so the
    signal never smooths a padded `None` into a number.
    """
    if not fast < slow:
        raise ValueError("fast period must be shorter than slow period")
    fast_line, slow_line = ema(values, fast), ema(values, slow)
    line: list[Decimal | None] = [None] * len(values)
    with localcontext(CANONICAL_CONTEXT):
        for index, (quick, slow_value) in enumerate(zip(fast_line, slow_line, strict=True)):
            if quick is not None and slow_value is not None:
                line[index] = quantized(quick - slow_value, PRICE_QUANTUM)
    defined = [(index, value) for index, value in enumerate(line) if value is not None]
    signal_line: list[Decimal | None] = [None] * len(values)
    histogram: list[Decimal | None] = [None] * len(values)
    if defined:
        smoothed = ema([value for _, value in defined], signal)
        with localcontext(CANONICAL_CONTEXT):
            for (index, value), smooth in zip(defined, smoothed, strict=True):
                if smooth is None:
                    continue
                signal_line[index] = smooth
                histogram[index] = quantized(value - smooth, PRICE_QUANTUM)
    return Macd(line, signal_line, histogram)


def rolling_average_volume(
    candles: Sequence[Candle], lookback: int = RELATIVE_VOLUME_LOOKBACK
) -> list[Decimal | None]:
    """Average volume of the `lookback` candles **before** each candle: the current one is never in its own baseline."""
    _require_period(lookback)
    result: list[Decimal | None] = [None] * len(candles)
    with localcontext(CANONICAL_CONTEXT):
        for index in range(lookback, len(candles)):
            window = candles[index - lookback : index]
            result[index] = quantized(sum((item.volume for item in window), ZERO) / lookback, PRICE_QUANTUM)
    return result


def relative_volume(candles: Sequence[Candle], lookback: int = RELATIVE_VOLUME_LOOKBACK) -> list[Decimal | None]:
    """Candle volume divided by the average of the previous `lookback` candles; None when that average is zero."""
    baseline = rolling_average_volume(candles, lookback)
    result: list[Decimal | None] = [None] * len(candles)
    with localcontext(CANONICAL_CONTEXT):
        for index, average in enumerate(baseline):
            if average is not None and average > ZERO:
                result[index] = quantize_ratio(candles[index].volume / average)
    return result


def returns(values: Sequence[Decimal]) -> list[Decimal | None]:
    """Simple close-to-close returns. Decimal has no logarithm, so simple returns are used and documented."""
    result: list[Decimal | None] = [None] * len(values)
    with localcontext(CANONICAL_CONTEXT):
        for index in range(1, len(values)):
            previous = values[index - 1]
            if previous != ZERO:
                result[index] = quantize_ratio((values[index] - previous) / previous)
    return result


def volatility(values: Sequence[Decimal], lookback: int = VOLATILITY_LOOKBACK) -> list[Decimal | None]:
    """Population standard deviation of the last `lookback` simple returns, as a ratio."""
    _require_period(lookback)
    changes = returns(values)
    result: list[Decimal | None] = [None] * len(values)
    with localcontext(CANONICAL_CONTEXT):
        for index in range(len(values)):
            window = [value for value in changes[max(0, index - lookback + 1) : index + 1] if value is not None]
            if len(window) < lookback:
                continue
            mean = sum(window, ZERO) / len(window)
            variance = sum(((value - mean) ** 2 for value in window), ZERO) / len(window)
            result[index] = quantize_ratio(variance.sqrt())
    return result


def distance_pct(price: Decimal, reference: Decimal | None) -> Decimal | None:
    """Signed distance from a reference level, in percent; None when the reference is missing or zero."""
    if reference is None or reference == ZERO:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return quantized((price - reference) / reference * HUNDRED, PCT_QUANTUM)


def slope_pct(series: Sequence[Decimal | None], span: int) -> Decimal | None:
    """Percent change of the last value of `series` against its value `span` candles earlier."""
    if span < 1:
        raise ValueError("span must be >= 1")
    if len(series) <= span:
        return None
    current, earlier = series[-1], series[-1 - span]
    if current is None or earlier is None or earlier == ZERO:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return quantized((current - earlier) / earlier * HUNDRED, PCT_QUANTUM)


def value_at(series: Sequence[Decimal | None], index: int) -> Decimal | None:
    """The value at `index`, or None when the index falls outside the series. Never wraps around."""
    if not 0 <= index < len(series):
        return None
    return series[index]


def slope_at(series: Sequence[Decimal | None], index: int, span: int) -> Decimal | None:
    """Percent change of `series[index]` against `series[index - span]`; only earlier values are ever read."""
    if span < 1:
        raise ValueError("span must be >= 1")
    current, earlier = value_at(series, index), value_at(series, index - span)
    if current is None or earlier is None or earlier == ZERO:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return quantized((current - earlier) / earlier * HUNDRED, PCT_QUANTUM)


@dataclass(frozen=True)
class IndicatorSeries:
    """Every indicator of one candle series, computed once and aligned with it.

    Spec 26: a scan must not recompute the whole history once per candle. The context engine, the feature
    snapshot and the backtester all read their values out of one of these, by index.
    """

    length: int
    ema: dict[int, list[Decimal | None]]
    rsi: list[Decimal | None]
    atr: list[Decimal | None]
    macd: Macd
    relative_volume: list[Decimal | None]
    average_volume: list[Decimal | None]
    volatility: list[Decimal | None]
    ema_periods: tuple[int, ...]
    rsi_period: int
    atr_period: int
    volume_lookback: int
    volatility_lookback: int

    def ema_at(self, period: int, index: int) -> Decimal | None:
        if period not in self.ema:
            raise KeyError(f"EMA {period} was not computed for this series")
        return value_at(self.ema[period], index)

    def rsi_at(self, index: int) -> Decimal | None:
        return value_at(self.rsi, index)

    def atr_at(self, index: int) -> Decimal | None:
        return value_at(self.atr, index)

    def relative_volume_at(self, index: int) -> Decimal | None:
        return value_at(self.relative_volume, index)

    def volatility_at(self, index: int) -> Decimal | None:
        return value_at(self.volatility, index)

    def macd_at(self, index: int) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        return (value_at(self.macd.macd, index), value_at(self.macd.signal, index),
                value_at(self.macd.histogram, index))

    def parameters(self) -> dict[str, object]:
        """The exact windows behind every value, so a stored snapshot stays explainable."""
        return {
            "ema_periods": list(self.ema_periods), "rsi_period": self.rsi_period, "atr_period": self.atr_period,
            "volume_lookback": self.volume_lookback, "volatility_lookback": self.volatility_lookback,
        }


def compute_series(
    candles: Sequence[Candle],
    *,
    ema_periods: tuple[int, ...] = EMA_PERIODS,
    rsi_period: int = RSI_PERIOD,
    atr_period: int = ATR_PERIOD,
    volume_lookback: int = RELATIVE_VOLUME_LOOKBACK,
    volatility_lookback: int = VOLATILITY_LOOKBACK,
) -> IndicatorSeries:
    """Every indicator of `candles` in one pass. Values stay `None` until their own window is filled."""
    values = closes(candles)
    return IndicatorSeries(
        length=len(candles),
        ema={period: ema(values, period) for period in ema_periods},
        rsi=rsi(values, rsi_period),
        atr=atr(candles, atr_period),
        macd=macd(values),
        relative_volume=relative_volume(candles, volume_lookback),
        average_volume=rolling_average_volume(candles, volume_lookback),
        volatility=volatility(values, volatility_lookback),
        ema_periods=tuple(ema_periods),
        rsi_period=rsi_period,
        atr_period=atr_period,
        volume_lookback=volume_lookback,
        volatility_lookback=volatility_lookback,
    )
