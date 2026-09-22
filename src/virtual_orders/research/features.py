"""The immutable feature snapshot of one candidate candle (Plan 5, D78).

A snapshot is everything the engine knew about a candle **at that candle**, in one flat, hashable record: the
pattern, the indicators, the market structure and the reused OHLCV pressure estimate. It is what a prediction
is later explained by, so it carries its own `feature_version` and a hash of its own contents.

Leak-free by construction: every input is read at an index, out of series that are causal by construction
(`indicators.py`) and out of a context that only reads confirmed pivots (`market_structure.py`). Nothing here
reaches forward, and `tests/research/test_leakage.py` proves it by rewriting the future and re-measuring.

The pressure estimate, VWAP, CMF and OBV slope are *not* recomputed here: they come from
`virtual_orders.analytics.pressure`, which owns their definition, and keep its disclaimer — they are an
estimate derived from OHLCV, never order-flow data.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any

from core.domain.hashing import CANONICAL_CONTEXT, sha256_hex
from virtual_orders.analytics.pressure import DISCLAIMER as PRESSURE_DISCLAIMER
from virtual_orders.analytics.pressure import METHOD as PRESSURE_METHOD
from virtual_orders.analytics.pressure import PressureEstimate, PressureSide
from virtual_orders.research.indicators import IndicatorSeries, distance_pct
from virtual_orders.research.market_structure import BreakoutState, GapState, MarketStructure
from virtual_orders.research.models import (
    ONE,
    ZERO,
    Candle,
    PatternDetection,
    PatternDirection,
    Timeframe,
    TrendState,
    quantize_ratio,
)

FEATURE_VERSION = "features-v2"


def session_position(candle: Candle, session_open: datetime, session_close: datetime) -> Decimal | None:
    """How far through its regular session the candle's last minute sits, in [0, 1].

    0 is the opening minute and 1 the closing one, so an opening-range candle and a closing-auction candle are
    distinguishable to a model without handing it a wall-clock time that means different things on a half day.
    """
    total = (session_close - session_open).total_seconds()
    if total <= 0:
        return None
    elapsed = (candle.end_ts - session_open).total_seconds()
    with localcontext(CANONICAL_CONTEXT):
        ratio = Decimal(int(elapsed)) / Decimal(int(total))
        return quantize_ratio(min(max(ratio, ZERO), ONE))


@dataclass(frozen=True)
class FeatureSnapshot:
    """One candle's context. Every field is Decimal, an enum value or None: never a float, never a nested object."""

    feature_version: str
    context_version: str
    ticker: str
    timeframe: Timeframe
    bar_ts: datetime
    bar_end_ts: datetime
    data_as_of: datetime

    pattern: str | None
    pattern_direction: PatternDirection | None
    pattern_engine_version: str | None
    pattern_score: Decimal | None
    pattern_geometry_score: Decimal | None
    pattern_context_score: Decimal | None

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    minutes_expected: int
    minutes_present: int
    candle_complete: bool

    ema9: Decimal | None
    ema20: Decimal | None
    ema50: Decimal | None
    ema200: Decimal | None
    ema20_slope_pct: Decimal | None
    ema9_distance_pct: Decimal | None
    ema20_distance_pct: Decimal | None
    ema50_distance_pct: Decimal | None
    ema200_distance_pct: Decimal | None

    rsi14: Decimal | None
    atr14: Decimal | None
    relative_atr_pct: Decimal | None
    relative_volume: Decimal | None
    volatility: Decimal | None
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None

    short_term_trend: TrendState
    medium_term_trend: TrendState
    breakout: BreakoutState
    gap: GapState
    gap_pct: Decimal | None
    support: Decimal | None
    resistance: Decimal | None
    support_distance_pct: Decimal | None
    resistance_distance_pct: Decimal | None

    pressure_estimate: bool
    pressure_method: str
    pressure_disclaimer: str
    vwap: Decimal | None
    vwap_distance_pct: Decimal | None
    cmf: Decimal | None
    obv_slope: Decimal | None
    close_location_value: Decimal | None
    pressure_side: str | None

    market_session_position: Decimal | None
    indicator_parameters: dict[str, Any]

    # What was observed, versus when we came to know it. `data_as_of` and the scan's own clock are provenance:
    # two scans over identical candles describe the same observation, and giving them different identities made
    # "how many occurrences exist" depend on how many times the scan ran.
    PROVENANCE_FIELDS = ("data_as_of",)

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def semantic_document(self) -> dict[str, Any]:
        """Everything the snapshot measured, with provenance removed."""
        return {name: value for name, value in self.as_document().items()
                if name not in self.PROVENANCE_FIELDS}

    @property
    def feature_hash(self) -> str:
        """Identity of the exact inputs a prediction was made from, independent of when they were read."""
        return sha256_hex(self.semantic_document())

    # The model's numeric columns, pinned by name and order. Deriving them from the annotations instead would
    # silently change a trained model's input layout the day a field is added, so the list is explicit and any
    # change to it is a `feature_version` change.
    NUMERIC_COLUMNS = (
        "close", "volume", "ema9_distance_pct", "ema20_distance_pct", "ema50_distance_pct", "ema200_distance_pct",
        "ema20_slope_pct", "rsi14", "atr14", "relative_atr_pct", "relative_volume", "volatility", "macd",
        "macd_signal", "macd_histogram", "gap_pct", "support_distance_pct", "resistance_distance_pct",
        "vwap_distance_pct", "cmf", "obv_slope", "close_location_value", "market_session_position",
        "pattern_score", "pattern_geometry_score", "pattern_context_score",
    )
    CATEGORICAL_COLUMNS = (
        "pattern", "pattern_direction", "short_term_trend", "medium_term_trend", "breakout", "gap",
        "pressure_side", "timeframe",
    )

    def numeric(self) -> dict[str, Decimal | None]:
        """The numeric features, in the pinned order: the ML dataset's only column source."""
        values: dict[str, Decimal | None] = {}
        for name in self.NUMERIC_COLUMNS:
            value = getattr(self, name)
            if value is not None and not isinstance(value, Decimal):
                raise TypeError(f"numeric feature {name} must be Decimal, got {type(value).__name__}")
            values[name] = value
        return values

    def categorical(self) -> dict[str, str | None]:
        """The categorical features, in the pinned order, as their plain string values."""
        return {name: (None if getattr(self, name) is None else str(getattr(self, name)))
                for name in self.CATEGORICAL_COLUMNS}


def build_snapshot(
    candles: list[Candle],
    index: int,
    *,
    ticker: str,
    data_as_of: datetime,
    series: IndicatorSeries,
    structure: MarketStructure,
    detection: PatternDetection | None = None,
    pressure: PressureEstimate | None = None,
    pressure_side: PressureSide | None = None,
    session_open: datetime | None = None,
    session_close: datetime | None = None,
) -> FeatureSnapshot:
    """Assemble the snapshot of `candles[index]`.

    `pressure` is the estimate the caller computed from the stored 1-minute bars **up to this candle's last
    minute** (`virtual_orders.analytics.pressure`); passing one computed over a later window would be a leak the
    caller introduced, so the scan and the backtester both cut it at the same instant as the candle.
    """
    if not 0 <= index < len(candles):
        raise IndexError("index outside the candle series")
    candle = candles[index]
    close = candle.close
    macd_value, macd_signal, macd_histogram = series.macd_at(index)
    emas = {period: series.ema_at(period, index) for period in (9, 20, 50, 200) if period in series.ema}
    position = (session_position(candle, session_open, session_close)
                if session_open is not None and session_close is not None else None)
    return FeatureSnapshot(
        feature_version=FEATURE_VERSION,
        context_version=structure.context_version,
        ticker=ticker,
        timeframe=candle.timeframe,
        bar_ts=candle.ts,
        bar_end_ts=candle.end_ts,
        data_as_of=data_as_of,
        pattern=None if detection is None else detection.pattern,
        pattern_direction=None if detection is None else detection.direction,
        pattern_engine_version=None if detection is None else detection.engine_version,
        pattern_score=None if detection is None else detection.overall_score,
        pattern_geometry_score=None if detection is None else detection.geometry_score,
        pattern_context_score=None if detection is None else detection.context_score,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=close,
        volume=candle.volume,
        minutes_expected=candle.minutes_expected,
        minutes_present=candle.minutes_present,
        candle_complete=candle.complete,
        ema9=emas.get(9),
        ema20=emas.get(20),
        ema50=emas.get(50),
        ema200=emas.get(200),
        ema20_slope_pct=structure.ema20_slope_pct,
        ema9_distance_pct=distance_pct(close, emas.get(9)),
        ema20_distance_pct=distance_pct(close, emas.get(20)),
        ema50_distance_pct=distance_pct(close, emas.get(50)),
        ema200_distance_pct=distance_pct(close, emas.get(200)),
        rsi14=series.rsi_at(index),
        atr14=series.atr_at(index),
        relative_atr_pct=structure.relative_atr_pct,
        relative_volume=series.relative_volume_at(index),
        volatility=series.volatility_at(index),
        macd=macd_value,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        short_term_trend=structure.short_term_trend,
        medium_term_trend=structure.medium_term_trend,
        breakout=structure.breakout,
        gap=structure.gap,
        gap_pct=structure.gap_pct,
        support=structure.support,
        resistance=structure.resistance,
        support_distance_pct=structure.support_distance_pct,
        resistance_distance_pct=structure.resistance_distance_pct,
        pressure_estimate=True,
        pressure_method=PRESSURE_METHOD,
        pressure_disclaimer=PRESSURE_DISCLAIMER,
        vwap=None if pressure is None else pressure.vwap,
        vwap_distance_pct=None if pressure is None else pressure.vwap_distance_pct,
        cmf=None if pressure is None else pressure.chaikin_money_flow,
        obv_slope=None if pressure is None else pressure.obv_slope,
        close_location_value=None if pressure is None else pressure.close_location_value,
        pressure_side=None if pressure_side is None else pressure_side.value,
        market_session_position=position,
        indicator_parameters=series.parameters(),
    )
