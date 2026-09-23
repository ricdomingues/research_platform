"""Candle geometry and the versioned, deterministic candlestick pattern engine (Plan 5, D75).

Two layers, deliberately separated:

* **Geometry** measures a candle (range, body, wicks, ratios, close location). It carries no trading meaning.
* **Patterns** combine geometry with the `PriorTrend` the caller computed from *earlier* candles only. This is
  what makes a long lower wick a Hammer after a decline and a Hanging Man after an advance: the same geometry,
  a different context. When the prior trend is unavailable (not enough history) or sideways, a pattern whose
  identity depends on it is simply not detected — never guessed.

Every threshold is a named constant of `PatternThresholds`, versioned with `ENGINE_VERSION` and hashed into the
research run's configuration, so a stored detection can always be explained by the rule that produced it.
Pure: no I/O, no platform imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from decimal import Decimal, localcontext
from typing import Any

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Bar
from virtual_orders.research.models import (
    ONE,
    ZERO,
    Candle,
    CandleDirection,
    Geometry,
    PatternDetection,
    PatternDirection,
    PriorTrend,
    Timeframe,
    TrendState,
    quantize_ratio,
    quantize_score,
)

ENGINE_VERSION = "candles-v1"
GEOMETRY_WEIGHT = Decimal("0.6")
CONTEXT_WEIGHT = Decimal("0.4")
TWO = Decimal(2)
HALF = Decimal("0.5")

# Context scores (D75). An unavailable prior scores 0: "unknown" is never read as "favourable".
CONTEXT_UNAVAILABLE = ZERO
CONTEXT_SIDEWAYS = Decimal("0.4")

SINGLE_CANDLE = ("DOJI", "DRAGONFLY_DOJI", "GRAVESTONE_DOJI", "HAMMER", "HANGING_MAN", "INVERTED_HAMMER",
                 "SHOOTING_STAR")
TWO_CANDLE = ("BULLISH_ENGULFING", "BEARISH_ENGULFING", "PIERCING_LINE", "DARK_CLOUD_COVER")
THREE_CANDLE = ("MORNING_STAR", "EVENING_STAR", "THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS")
# Each family with the number of candles a reading of it spans. `SUPPORTED_PATTERNS` and
# `MAX_PATTERN_CANDLES` are derived from it rather than written down twice, which is what pins the identity
# window: `identity.py` hashes a span this wide, because a reading at one bucket is a function of every
# candle its pattern reaches back over, not of that bucket's own bars alone. It does NOT yet make a fourth
# family safe to add -- `detect_at` below still dispatches on a hardcoded (1, 2, 3) and `backtest.py`'s
# `PATTERN_LENGTHS` still spells the same three out -- so a new family means editing those too.
PATTERN_FAMILIES: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, SINGLE_CANDLE), (2, TWO_CANDLE), (3, THREE_CANDLE),
)
SUPPORTED_PATTERNS = tuple(name for _, family in PATTERN_FAMILIES for name in family)
MAX_PATTERN_CANDLES = max(span for span, _ in PATTERN_FAMILIES)
# Continuation patterns want a prior trend that agrees with them; every other pattern is a reversal and wants
# one that opposes it. The distinction only ever changes `context_score`, never whether the geometry matched.
CONTINUATION_PATTERNS = frozenset({"THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS"})


@dataclass(frozen=True)
class PatternThresholds:
    """Versioned rule constants. Never inline a magic number in a detector: add a field here."""

    doji_body_ratio: Decimal = Decimal("0.05")
    doji_wick_ratio_max: Decimal = Decimal("0.10")
    doji_wick_ratio_min: Decimal = Decimal("0.60")
    hammer_wick_ratio_min: Decimal = Decimal("0.50")
    hammer_opposite_wick_ratio_max: Decimal = Decimal("0.15")
    hammer_body_ratio_max: Decimal = Decimal("0.35")
    hammer_wick_to_body: Decimal = Decimal("2")
    engulf_body_ratio_min: Decimal = Decimal("1")
    piercing_penetration_min: Decimal = Decimal("0.5")
    star_body_ratio_max: Decimal = Decimal("0.35")
    star_long_body_ratio_min: Decimal = Decimal("0.50")
    soldiers_body_ratio_min: Decimal = Decimal("0.50")
    soldiers_opposite_wick_ratio_max: Decimal = Decimal("0.25")

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{item.name} must be Decimal")
            if value <= ZERO:
                raise ValueError(f"{item.name} must be positive")

    def snapshot(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


DEFAULT_THRESHOLDS = PatternThresholds()


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == ZERO:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return quantize_ratio(numerator / denominator)


def measure(open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> Geometry:
    """Geometry of one OHLC quadruple. A zero-range candle keeps every ratio at None, explicitly."""
    with localcontext(CANONICAL_CONTEXT):
        span = high - low
        body = abs(close - open_)
        upper = high - max(open_, close)
        lower = min(open_, close) - low
    direction = (CandleDirection.BULLISH if close > open_ else
                 CandleDirection.BEARISH if close < open_ else CandleDirection.FLAT)
    return Geometry(
        range=span, body=body, upper_wick=upper, lower_wick=lower, body_ratio=_ratio(body, span),
        upper_wick_ratio=_ratio(upper, span), lower_wick_ratio=_ratio(lower, span),
        close_location=_ratio(close - low, span), direction=direction, zero_range=span == ZERO,
    )


def geometry(candle: Candle) -> Geometry:
    return measure(candle.open, candle.high, candle.low, candle.close)


def bar_geometry(bar: Bar) -> Geometry:
    """The same measurement over a stored 1-minute bar, so 1m candles can be measured without resampling."""
    return measure(bar.open, bar.high, bar.low, bar.close)


def _fraction_below(value: Decimal, threshold: Decimal) -> Decimal:
    """1 when `value` is 0, 0 when it reaches `threshold`: how comfortably a "must be small" rule was met."""
    with localcontext(CANONICAL_CONTEXT):
        return quantize_score((threshold - value) / threshold)


def _fraction_above(value: Decimal, threshold: Decimal, ceiling: Decimal = ONE) -> Decimal:
    """0 at `threshold`, 1 at `ceiling`: how comfortably a "must be large" rule was met."""
    if ceiling <= threshold:
        raise ValueError("ceiling must be above threshold")
    with localcontext(CANONICAL_CONTEXT):
        return quantize_score((value - threshold) / (ceiling - threshold))


def _mean(values: Sequence[Decimal]) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        return quantize_score(sum(values, ZERO) / len(values))


def context_score(pattern: str, direction: PatternDirection, prior: PriorTrend) -> Decimal:
    """How well the prior move fits the pattern's meaning (D75).

    A reversal pattern wants a prior trend that *opposes* its direction; a continuation pattern wants one that
    agrees. The stronger that trend, the higher the score. An unavailable prior scores 0 and a sideways one a
    fixed middle value, so a detection is never flattered by missing context.
    """
    if not prior.available or prior.state is TrendState.UNKNOWN:
        return quantize_score(CONTEXT_UNAVAILABLE)
    if prior.state is TrendState.SIDEWAYS:
        return quantize_score(CONTEXT_SIDEWAYS)
    rising = prior.state is TrendState.UP
    bullish = direction is PatternDirection.BULLISH
    agrees = rising is bullish
    wanted = agrees if pattern in CONTINUATION_PATTERNS else not agrees
    with localcontext(CANONICAL_CONTEXT):
        return quantize_score(HALF + prior.strength / TWO if wanted else (ONE - prior.strength) / TWO)


def _overall(geometry_score: Decimal, context: Decimal) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        return quantize_score(geometry_score * GEOMETRY_WEIGHT + context * CONTEXT_WEIGHT)


def _detection(
    pattern: str,
    direction: PatternDirection,
    window: Sequence[Candle],
    geometry_score: Decimal,
    prior: PriorTrend,
    evidence: dict[str, Any],
) -> PatternDetection:
    context = context_score(pattern, direction, prior)
    return PatternDetection(
        pattern=pattern, engine_version=ENGINE_VERSION, direction=direction, timeframe=window[-1].timeframe,
        start_ts=window[0].ts, end_ts=window[-1].end_ts, candles=len(window), geometry_score=geometry_score,
        context_score=context, overall_score=_overall(geometry_score, context),
        evidence={**evidence, **prior.evidence(), "weights": {"geometry": GEOMETRY_WEIGHT, "context": CONTEXT_WEIGHT}},
    )


def _doji_family(
    candle: Candle, shape: Geometry, prior: PriorTrend, limits: PatternThresholds
) -> list[PatternDetection]:
    body_ratio, upper, lower = shape.body_ratio, shape.upper_wick_ratio, shape.lower_wick_ratio
    if body_ratio is None or upper is None or lower is None or body_ratio > limits.doji_body_ratio:
        return []
    window = [candle]
    base: dict[str, Any] = {"body_ratio": body_ratio, "upper_wick_ratio": upper, "lower_wick_ratio": lower,
                            "close_location": shape.close_location}
    tightness = _fraction_below(body_ratio, limits.doji_body_ratio)
    found = [_detection("DOJI", PatternDirection.NEUTRAL, window, tightness, prior, base)]
    if upper <= limits.doji_wick_ratio_max and lower >= limits.doji_wick_ratio_min:
        found.append(_detection("DRAGONFLY_DOJI", PatternDirection.BULLISH, window,
                                _mean([tightness, _fraction_above(lower, limits.doji_wick_ratio_min)]), prior, base))
    if lower <= limits.doji_wick_ratio_max and upper >= limits.doji_wick_ratio_min:
        found.append(_detection("GRAVESTONE_DOJI", PatternDirection.BEARISH, window,
                                _mean([tightness, _fraction_above(upper, limits.doji_wick_ratio_min)]), prior, base))
    return found


def _long_wick_family(
    candle: Candle, shape: Geometry, prior: PriorTrend, limits: PatternThresholds
) -> list[PatternDetection]:
    """Hammer/Hanging Man and Inverted Hammer/Shooting Star: one geometry each, named by the prior trend."""
    body_ratio, upper, lower = shape.body_ratio, shape.upper_wick_ratio, shape.lower_wick_ratio
    if body_ratio is None or upper is None or lower is None or body_ratio > limits.hammer_body_ratio_max:
        return []
    if not prior.available or prior.state not in (TrendState.UP, TrendState.DOWN):
        return []  # D75: without a prior move this geometry has no name, so nothing is recorded
    after_decline = prior.state is TrendState.DOWN
    window = [candle]
    found: list[PatternDetection] = []
    with localcontext(CANONICAL_CONTEXT):
        body_multiple = limits.hammer_wick_to_body * shape.body
    for long_side, short_side, wick, bullish_name, bearish_name in (
        (lower, upper, shape.lower_wick, "HAMMER", "HANGING_MAN"),
        (upper, lower, shape.upper_wick, "INVERTED_HAMMER", "SHOOTING_STAR"),
    ):
        if long_side < limits.hammer_wick_ratio_min or short_side > limits.hammer_opposite_wick_ratio_max:
            continue
        if wick < body_multiple:
            continue
        pattern = bullish_name if after_decline else bearish_name
        direction = PatternDirection.BULLISH if after_decline else PatternDirection.BEARISH
        score = _mean([
            _fraction_above(long_side, limits.hammer_wick_ratio_min),
            _fraction_below(body_ratio, limits.hammer_body_ratio_max),
            _fraction_below(short_side, limits.hammer_opposite_wick_ratio_max),
        ])
        found.append(_detection(pattern, direction, window, score, prior, {
            "body_ratio": body_ratio, "upper_wick_ratio": upper, "lower_wick_ratio": lower,
            "close_location": shape.close_location, "wick_to_body_multiple": _ratio(wick, shape.body),
        }))
    return found


def _engulfing(
    previous: Candle, current: Candle, prior: PriorTrend, limits: PatternThresholds
) -> list[PatternDetection]:
    before, now = geometry(previous), geometry(current)
    if before.body == ZERO:
        return []  # a prior doji has no body to engulf: the rule would divide by zero and mean nothing
    engulf_ratio = _ratio(now.body, before.body)
    if engulf_ratio is None or engulf_ratio <= limits.engulf_body_ratio_min:
        return []
    window = [previous, current]
    evidence: dict[str, Any] = {
        "previous_direction": before.direction, "body_ratio": now.body_ratio, "engulf_ratio": engulf_ratio,
        "previous_open": previous.open, "previous_close": previous.close,
    }
    score = _fraction_above(min(engulf_ratio, TWO), limits.engulf_body_ratio_min, TWO)
    if (before.direction is CandleDirection.BEARISH and now.direction is CandleDirection.BULLISH
            and current.open < previous.close and current.close > previous.open):
        return [_detection("BULLISH_ENGULFING", PatternDirection.BULLISH, window, score, prior,
                           {**evidence, "close_above_previous_open": True})]
    if (before.direction is CandleDirection.BULLISH and now.direction is CandleDirection.BEARISH
            and current.open > previous.close and current.close < previous.open):
        return [_detection("BEARISH_ENGULFING", PatternDirection.BEARISH, window, score, prior,
                           {**evidence, "close_below_previous_open": True})]
    return []


def _piercing(
    previous: Candle, current: Candle, prior: PriorTrend, limits: PatternThresholds
) -> list[PatternDetection]:
    before, now = geometry(previous), geometry(current)
    if before.body == ZERO:
        return []
    window = [previous, current]
    if (before.direction is CandleDirection.BEARISH and now.direction is CandleDirection.BULLISH
            and current.open < previous.close and current.close < previous.open):
        penetration = _ratio(current.close - previous.close, before.body)
        if penetration is not None and penetration >= limits.piercing_penetration_min:
            return [_detection("PIERCING_LINE", PatternDirection.BULLISH, window,
                               _fraction_above(penetration, limits.piercing_penetration_min), prior,
                               {"previous_direction": before.direction, "penetration": penetration,
                                "opened_below_previous_close": True, "closed_below_previous_open": True})]
    if (before.direction is CandleDirection.BULLISH and now.direction is CandleDirection.BEARISH
            and current.open > previous.close and current.close > previous.open):
        penetration = _ratio(previous.close - current.close, before.body)
        if penetration is not None and penetration >= limits.piercing_penetration_min:
            return [_detection("DARK_CLOUD_COVER", PatternDirection.BEARISH, window,
                               _fraction_above(penetration, limits.piercing_penetration_min), prior,
                               {"previous_direction": before.direction, "penetration": penetration,
                                "opened_above_previous_close": True, "closed_above_previous_open": True})]
    return []


def _star(
    first: Candle, star: Candle, last: Candle, prior: PriorTrend, limits: PatternThresholds
) -> list[PatternDetection]:
    one, middle, three = geometry(first), geometry(star), geometry(last)
    if one.body_ratio is None or middle.body_ratio is None or three.body_ratio is None:
        return []
    if one.body_ratio < limits.star_long_body_ratio_min or middle.body_ratio > limits.star_body_ratio_max:
        return []
    window = [first, star, last]
    with localcontext(CANONICAL_CONTEXT):
        midpoint = (first.open + first.close) / TWO
    score = _mean([
        _fraction_below(middle.body_ratio, limits.star_body_ratio_max),
        _fraction_above(one.body_ratio, limits.star_long_body_ratio_min),
        _fraction_above(three.body_ratio, limits.star_long_body_ratio_min) if three.body_ratio
        >= limits.star_long_body_ratio_min else ZERO,
    ])
    evidence: dict[str, Any] = {
        "first_body_ratio": one.body_ratio, "star_body_ratio": middle.body_ratio, "last_body_ratio": three.body_ratio,
        "first_body_midpoint": midpoint,
    }
    if (one.direction is CandleDirection.BEARISH and three.direction is CandleDirection.BULLISH
            and max(star.open, star.close) < first.close and last.close > midpoint):
        return [_detection("MORNING_STAR", PatternDirection.BULLISH, window, score, prior,
                           {**evidence, "star_below_first_body": True, "closed_above_midpoint": True})]
    if (one.direction is CandleDirection.BULLISH and three.direction is CandleDirection.BEARISH
            and min(star.open, star.close) > first.close and last.close < midpoint):
        return [_detection("EVENING_STAR", PatternDirection.BEARISH, window, score, prior,
                           {**evidence, "star_above_first_body": True, "closed_below_midpoint": True})]
    return []


def _marching(
    window: Sequence[Candle], prior: PriorTrend, limits: PatternThresholds
) -> list[PatternDetection]:
    """Three White Soldiers / Three Black Crows: three real bodies marching, each opening inside the last one."""
    shapes = [geometry(candle) for candle in window]
    if any(shape.body_ratio is None or shape.body_ratio < limits.soldiers_body_ratio_min for shape in shapes):
        return []
    ratios = [shape.body_ratio for shape in shapes if shape.body_ratio is not None]
    for name, direction, wick, ordered in (
        ("THREE_WHITE_SOLDIERS", PatternDirection.BULLISH, "upper_wick_ratio", CandleDirection.BULLISH),
        ("THREE_BLACK_CROWS", PatternDirection.BEARISH, "lower_wick_ratio", CandleDirection.BEARISH),
    ):
        if any(shape.direction is not ordered for shape in shapes):
            continue
        opposite = [getattr(shape, wick) for shape in shapes]
        if any(value is None or value > limits.soldiers_opposite_wick_ratio_max for value in opposite):
            continue
        rising = ordered is CandleDirection.BULLISH
        closes_march = all(
            (later.close > earlier.close) if rising else (later.close < earlier.close)
            for earlier, later in zip(window, window[1:], strict=False)
        )
        opens_inside = all(
            min(earlier.open, earlier.close) <= later.open <= max(earlier.open, earlier.close)
            for earlier, later in zip(window, window[1:], strict=False)
        )
        if not (closes_march and opens_inside):
            continue
        score = _mean([_fraction_above(value, limits.soldiers_body_ratio_min) for value in ratios])
        return [_detection(name, direction, window, score, prior, {
            "body_ratios": ratios, "closes_march": True, "opens_inside_previous_body": True,
            "opposite_wick_ratios": opposite,
        })]
    return []


@dataclass(frozen=True)
class PriorContext:
    """The prior trend before a pattern's **own first candle**, keyed by how many candles the pattern spans.

    A single `PriorTrend` cannot serve patterns of different lengths: the trend that names a one-candle Hammer
    ends at the candle before it, while the trend before a three-candle Morning Star must end three candles
    earlier. Reusing one value for all of them would fold the pattern's own first candles into the context that
    is supposed to describe what came before it. The scan therefore builds one entry per length.
    """

    by_length: dict[int, PriorTrend]

    def for_length(self, candles: int) -> PriorTrend:
        return self.by_length.get(candles, PriorTrend(TrendState.UNKNOWN, ZERO, 0, False))

    @classmethod
    def fixed(cls, prior: PriorTrend) -> PriorContext:
        """One trend for every length: convenient for a unit test over a hand-built window, never for a scan."""
        return cls({length: prior for length in (1, 2, 3)})


def detect_at(
    candles: Sequence[Candle],
    index: int,
    *,
    prior: PriorTrend | PriorContext,
    thresholds: PatternThresholds = DEFAULT_THRESHOLDS,
) -> list[PatternDetection]:
    """Every pattern whose **last** candle is `candles[index]`, sorted by name.

    `prior` describes the trend before each pattern's first candle and must be computed from candles strictly
    earlier than that one: this function never looks beyond `index`, which is what keeps a historical scan free
    of look-ahead. Overlapping detections are all returned (a Dragonfly Doji is also a Doji); choosing between
    them is the setup layer's job, not the pattern engine's.
    """
    if not 0 <= index < len(candles):
        raise IndexError("index outside the candle series")
    current = candles[index]
    if any(candle.timeframe is not current.timeframe for candle in candles):
        raise ValueError("a detection window must hold one timeframe only")
    context = prior if isinstance(prior, PriorContext) else PriorContext.fixed(prior)
    one, two, three = (context.for_length(length) for length in (1, 2, 3))
    shape = geometry(current)
    found = _doji_family(current, shape, one, thresholds) + _long_wick_family(current, shape, one, thresholds)
    if index >= 1:
        previous = candles[index - 1]
        found += _engulfing(previous, current, two, thresholds)
        found += _piercing(previous, current, two, thresholds)
    if index >= 2:
        first, star = candles[index - 2], candles[index - 1]
        found += _star(first, star, current, three, thresholds)
        found += _marching([first, star, current], three, thresholds)
    return sorted(found, key=lambda item: item.pattern)


def timeframe_of(candles: Sequence[Candle]) -> Timeframe:
    if not candles:
        raise ValueError("an empty series has no timeframe")
    return candles[0].timeframe
