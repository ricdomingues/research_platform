"""Buy/sell pressure ESTIMATES derived from 1-minute OHLCV (owner 2026-09-13, D27). Pure and deterministic.

This is not order-flow data and not the roadmap's Pressure Engine: every consumer labels it as an estimate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Bar

METHOD = "OHLCV_PRESSURE_ESTIMATE_V1"
DISCLAIMER = "Estimate derived from 1-minute OHLCV bars; it is not order-flow or trade-side data."
QUANTUM = Decimal("0.0001")
ZERO = Decimal(0)
HUNDRED = Decimal(100)
THREE = Decimal(3)


class PressureSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class PressureEstimate:
    method: str
    bars: int
    first_bar_ts: datetime
    last_bar_ts: datetime
    close_location_value: Decimal
    chaikin_money_flow: Decimal
    obv_slope: Decimal
    vwap: Decimal
    vwap_distance_pct: Decimal


def _quantized(value: Decimal) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        result = value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    return result if result != 0 else ZERO.quantize(QUANTUM)  # never "-0.0000"


def close_location_value(bar: Bar) -> Decimal:
    """((close - low) - (high - close)) / (high - low), in [-1, 1]; 0 for a zero-range bar."""
    span = bar.high - bar.low
    if span == 0:
        return ZERO
    with localcontext(CANONICAL_CONTEXT):
        return ((bar.close - bar.low) - (bar.high - bar.close)) / span


def estimate_pressure(bars: Sequence[Bar]) -> PressureEstimate | None:
    ordered = sorted(bars, key=lambda item: item.ts)
    if len({item.ts for item in ordered}) != len(ordered):
        raise ValueError("duplicate bar minute")
    if len(ordered) < 2:
        return None
    with localcontext(CANONICAL_CONTEXT):
        volume = sum((item.volume for item in ordered), ZERO)
        flow = sum((close_location_value(item) * item.volume for item in ordered), ZERO)
        cmf = ZERO if volume == 0 else flow / volume
        obv = ZERO
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.close > previous.close:
                obv += current.volume
            elif current.close < previous.close:
                obv -= current.volume
        mean_volume = volume / len(ordered)
        slope = ZERO if mean_volume == 0 else obv / (len(ordered) - 1) / mean_volume
        last_close = ordered[-1].close
        typical = sum(((item.high + item.low + item.close) / THREE * item.volume for item in ordered), ZERO)
        vwap = last_close if volume == 0 else typical / volume
        distance = ZERO if vwap == 0 else (last_close - vwap) / vwap * HUNDRED
    return PressureEstimate(
        method=METHOD, bars=len(ordered), first_bar_ts=ordered[0].ts, last_bar_ts=ordered[-1].ts,
        close_location_value=_quantized(close_location_value(ordered[-1])), chaikin_money_flow=_quantized(cmf),
        obv_slope=_quantized(slope), vwap=_quantized(vwap), vwap_distance_pct=_quantized(distance),
    )


def strong_pressure(estimate: PressureEstimate, cmf_threshold: Decimal) -> PressureSide | None:
    """BUY: CMF >= threshold, rising OBV and close at/above VWAP. SELL is the exact mirror."""
    if estimate.chaikin_money_flow >= cmf_threshold and estimate.obv_slope > 0 and estimate.vwap_distance_pct >= 0:
        return PressureSide.BUY
    if estimate.chaikin_money_flow <= -cmf_threshold and estimate.obv_slope < 0 and estimate.vwap_distance_pct <= 0:
        return PressureSide.SELL
    return None
