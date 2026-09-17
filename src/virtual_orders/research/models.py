"""Immutable research types shared by the timeframe, candlestick, context and setup engines (Plan 5, D74).

Pure: no I/O, no platform imports. Decimal everywhere; floats appear only at the ML and chart borders.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from typing import Any

from core.domain.hashing import CANONICAL_CONTEXT, sha256_hex

ZERO = Decimal(0)
ONE = Decimal(1)
RATIO_QUANTUM = Decimal("0.0001")
SCORE_QUANTUM = Decimal("0.0001")


class Timeframe(StrEnum):
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


SESSION_TIMEFRAME = None  # the bucket size of Timeframe.D1: one whole regular session, however long it is
TIMEFRAME_MINUTES: dict[Timeframe, int | None] = {
    Timeframe.M5: 5, Timeframe.M15: 15, Timeframe.M30: 30, Timeframe.H1: 60, Timeframe.H4: 240,
    Timeframe.D1: SESSION_TIMEFRAME,
}


def bucket_minutes(timeframe: Timeframe) -> int | None:
    """Nominal expected minutes per candle; None for D1, whose bucket is the whole regular session."""
    return TIMEFRAME_MINUTES[timeframe]


class CandleDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    FLAT = "FLAT"


class PatternDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class TrendState(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"
    UNKNOWN = "UNKNOWN"


def quantized(value: Decimal, quantum: Decimal) -> Decimal:
    """Half-even in the canonical context, never a negative zero (the repository-wide rule)."""
    with localcontext(CANONICAL_CONTEXT):
        result = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    return result if result != 0 else ZERO.quantize(quantum)


def quantize_ratio(value: Decimal) -> Decimal:
    return quantized(value, RATIO_QUANTUM)


def quantize_score(value: Decimal) -> Decimal:
    """Scores live in [0, 1]: the formula is clamped before quantizing, never allowed to drift outside."""
    with localcontext(CANONICAL_CONTEXT):
        clamped = min(max(value, ZERO), ONE)
    return quantized(clamped, SCORE_QUANTUM)


def _require_aware_minute(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    moment = value.astimezone(UTC)
    if moment.second or moment.microsecond:
        raise ValueError(f"{name} must be a whole minute")
    return moment


@dataclass(frozen=True)
class Candle:
    """One aggregated candle of `timeframe`, built only from completed 1-minute bars of a single session.

    `ts` is the first expected minute of the bucket and `end_ts` the last, so the candle covers
    `[ts, end_ts + 1min)`. `minutes_expected` counts the session minutes the bucket spans and
    `minutes_present` how many of them had a stored bar: a candle is never silently built as if a missing
    minute did not exist. `truncated` marks a bucket the session close cut short (a half day, or the last
    bucket of a regular session).
    """

    timeframe: Timeframe
    ts: datetime
    end_ts: datetime
    session_day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    minutes_expected: int
    minutes_present: int
    truncated: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "timeframe", Timeframe(self.timeframe))
        for name in ("open", "high", "low", "close", "volume"):
            if not isinstance(getattr(self, name), Decimal):
                raise TypeError(f"candle {name} must be Decimal")
        object.__setattr__(self, "ts", _require_aware_minute(self.ts, "ts"))
        object.__setattr__(self, "end_ts", _require_aware_minute(self.end_ts, "end_ts"))
        if self.end_ts < self.ts:
            raise ValueError("end_ts must be at or after ts")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("inconsistent OHLC")
        if self.minutes_expected < 1:
            raise ValueError("minutes_expected must be >= 1")
        if not 1 <= self.minutes_present <= self.minutes_expected:
            raise ValueError("minutes_present must be between 1 and minutes_expected")

    @property
    def complete(self) -> bool:
        """Every expected minute of the bucket had a stored bar."""
        return self.minutes_present == self.minutes_expected

    @property
    def direction(self) -> CandleDirection:
        if self.close > self.open:
            return CandleDirection.BULLISH
        if self.close < self.open:
            return CandleDirection.BEARISH
        return CandleDirection.FLAT


@dataclass(frozen=True)
class Geometry:
    """Pure measurement of one candle. It carries no trading meaning: a long lower wick is not a Hammer."""

    range: Decimal
    body: Decimal
    upper_wick: Decimal
    lower_wick: Decimal
    body_ratio: Decimal | None
    upper_wick_ratio: Decimal | None
    lower_wick_ratio: Decimal | None
    close_location: Decimal | None
    direction: CandleDirection
    zero_range: bool

    def evidence(self) -> dict[str, Any]:
        return {
            "range": self.range, "body": self.body, "upper_wick": self.upper_wick, "lower_wick": self.lower_wick,
            "body_ratio": self.body_ratio, "upper_wick_ratio": self.upper_wick_ratio,
            "lower_wick_ratio": self.lower_wick_ratio, "close_location": self.close_location,
            "direction": self.direction, "zero_range": self.zero_range,
        }


@dataclass(frozen=True)
class PriorTrend:
    """The context in force *before* a pattern's first candle (market_structure.py computes it).

    `available` is false when there is not enough history to judge: a pattern whose meaning depends on the
    prior move (Hammer vs Hanging Man) is then not classified at all, instead of guessing a direction.
    """

    state: TrendState
    strength: Decimal
    lookback: int
    available: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", TrendState(self.state))
        if not isinstance(self.strength, Decimal):
            raise TypeError("strength must be Decimal")
        if not ZERO <= self.strength <= ONE:
            raise ValueError("strength must be in [0, 1]")
        if self.available and self.state is TrendState.UNKNOWN:
            raise ValueError("an available prior trend cannot be UNKNOWN")

    def evidence(self) -> dict[str, Any]:
        return {"prior_trend": self.state, "prior_trend_strength": self.strength, "prior_trend_lookback": self.lookback}


UNKNOWN_PRIOR = PriorTrend(TrendState.UNKNOWN, ZERO, 0, False)


@dataclass(frozen=True)
class PatternDetection:
    """One versioned, deterministic detection. `evidence` explains why the rule matched, in Decimal terms."""

    pattern: str
    engine_version: str
    direction: PatternDirection
    timeframe: Timeframe
    start_ts: datetime
    end_ts: datetime
    candles: int
    geometry_score: Decimal
    context_score: Decimal
    overall_score: Decimal
    evidence: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "direction", PatternDirection(self.direction))
        object.__setattr__(self, "timeframe", Timeframe(self.timeframe))
        object.__setattr__(self, "start_ts", _require_aware_minute(self.start_ts, "start_ts"))
        object.__setattr__(self, "end_ts", _require_aware_minute(self.end_ts, "end_ts"))
        if self.end_ts < self.start_ts:
            raise ValueError("end_ts must be at or after start_ts")
        if self.candles < 1:
            raise ValueError("candles must be >= 1")
        for name in ("geometry_score", "context_score", "overall_score"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be Decimal")
            if not ZERO <= value <= ONE:
                raise ValueError(f"{name} must be in [0, 1]")

    def detection_key(self, ticker: str) -> str:
        """Identity of a detection, independent of when the scan ran (repository.py enforces it)."""
        return f"{self.engine_version}:{ticker}:{self.timeframe.value}:{self.pattern}:{self.end_ts.isoformat()}"

    @property
    def evidence_hash(self) -> str:
        """Identity of *what* was detected. A vendor correction that changes the geometry changes this hash,
        so the corrected detection is stored beside the original instead of overwriting it or conflicting."""
        return sha256_hex({
            "pattern": self.pattern, "engine_version": self.engine_version, "direction": self.direction,
            "timeframe": self.timeframe, "start_ts": self.start_ts, "end_ts": self.end_ts, "candles": self.candles,
            "geometry_score": self.geometry_score, "context_score": self.context_score,
            "overall_score": self.overall_score, "evidence": self.evidence,
        })

    def as_document(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}
