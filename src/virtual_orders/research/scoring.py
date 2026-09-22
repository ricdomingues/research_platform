"""The deterministic setup score and what it is allowed to mean (Plan 5, D82).

`deterministic_score` is a weighted composite of things already measured elsewhere: the pattern's own
geometry-and-context score, trend alignment, relative volume, the OHLCV pressure estimate and how much room the
structure leaves towards the objective. Every weight is named, versioned and hashed into the research run.

**It is not a probability.** `SCORE_INTERPRETATION` is carried next to the number everywhere it is stored or
displayed, because a bare 0-to-1 number beside a ticker invites exactly the reading it does not support. The
only probability in this sub-project is `ml_probability`, which answers a specific question
(P(target before stop)) and is always reported with its own model and feature versions.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal, localcontext
from typing import Any

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Direction
from virtual_orders.research.features import FeatureSnapshot
from virtual_orders.research.market_structure import BreakoutState
from virtual_orders.research.models import ONE, ZERO, PatternDetection, TrendState, quantize_score

SCORING_VERSION = "scoring-v1"
SCORE_INTERPRETATION = (
    "Deterministic composite in [0, 1] of the pattern's geometry and context score, trend alignment, relative "
    "volume, the OHLCV pressure ESTIMATE and structural room. It ranks research observations against each "
    "other under one fixed rule; it is not a probability, not an expected return and not a recommendation. "
    "P(target before stop) is reported separately as ml_probability, with its own model and feature versions."
)
NEUTRAL_COMPONENT = Decimal("0.5")  # what an unavailable input scores: never 0 (a penalty) and never 1 (a reward)
STRONG_RELATIVE_VOLUME = Decimal("2")
WEAK_RELATIVE_VOLUME = Decimal("0.5")
STRONG_CMF = Decimal("0.15")


@dataclass(frozen=True)
class ScoreWeights:
    """Versioned weights. They must sum to 1 so the composite stays inside [0, 1] by construction."""

    pattern: Decimal = Decimal("0.40")
    trend: Decimal = Decimal("0.20")
    volume: Decimal = Decimal("0.15")
    pressure: Decimal = Decimal("0.15")
    structure: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{item.name} must be Decimal")
            if value < ZERO:
                raise ValueError(f"{item.name} must not be negative")
        with localcontext(CANONICAL_CONTEXT):
            total = sum((getattr(self, item.name) for item in fields(self)), ZERO)
        if total != ONE:
            raise ValueError(f"weights must sum to 1, got {total}")

    def snapshot(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


DEFAULT_WEIGHTS = ScoreWeights()


def _scaled(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    """`low` maps to 0 and `high` to 1, clamped outside that range."""
    if high <= low:
        raise ValueError("high must be above low")
    with localcontext(CANONICAL_CONTEXT):
        return quantize_score((value - low) / (high - low))


def trend_component(snapshot: FeatureSnapshot, direction: Direction) -> Decimal:
    """1 when both horizons agree with the trade, 0 when both oppose it, 0.5 when they disagree or are unknown."""
    wanted = TrendState.UP if direction is Direction.LONG else TrendState.DOWN
    against = TrendState.DOWN if direction is Direction.LONG else TrendState.UP
    score = ZERO
    known = 0
    for state in (snapshot.short_term_trend, snapshot.medium_term_trend):
        if state is TrendState.UNKNOWN:
            continue
        known += 1
        if state is wanted:
            score += ONE
        elif state is not against:
            score += NEUTRAL_COMPONENT  # sideways is neither confirmation nor contradiction
    if known == 0:
        return quantize_score(NEUTRAL_COMPONENT)
    with localcontext(CANONICAL_CONTEXT):
        return quantize_score(score / known)


def volume_component(snapshot: FeatureSnapshot) -> Decimal:
    """Relative volume mapped from 0.5x (0) to 2x (1). Conviction shows up as participation, not as price alone."""
    if snapshot.relative_volume is None:
        return quantize_score(NEUTRAL_COMPONENT)
    return _scaled(snapshot.relative_volume, WEAK_RELATIVE_VOLUME, STRONG_RELATIVE_VOLUME)


def pressure_component(snapshot: FeatureSnapshot, direction: Direction) -> Decimal:
    """The OHLCV pressure ESTIMATE, read only as agreement with the trade's side (never as order flow)."""
    if snapshot.cmf is None:
        return quantize_score(NEUTRAL_COMPONENT)
    signed = snapshot.cmf if direction is Direction.LONG else -snapshot.cmf
    return _scaled(signed, -STRONG_CMF, STRONG_CMF)


def structure_component(snapshot: FeatureSnapshot, direction: Direction) -> Decimal:
    """Room towards the objective: a long scores well below resistance, a short well above support."""
    towards = snapshot.resistance_distance_pct if direction is Direction.LONG else snapshot.support_distance_pct
    wanted_breakout = (BreakoutState.ABOVE_RESISTANCE if direction is Direction.LONG
                       else BreakoutState.BELOW_SUPPORT)
    parts: list[Decimal] = []
    if towards is not None:
        # The distance is signed away from price; only its magnitude matters, capped at 5% so a far-away level
        # does not dominate the composite.
        parts.append(_scaled(min(abs(towards), Decimal("5")), ZERO, Decimal("5")))
    if snapshot.breakout is not BreakoutState.UNKNOWN:
        parts.append(ONE if snapshot.breakout is wanted_breakout else
                     NEUTRAL_COMPONENT if snapshot.breakout is BreakoutState.INSIDE else ZERO)
    if not parts:
        return quantize_score(NEUTRAL_COMPONENT)
    with localcontext(CANONICAL_CONTEXT):
        return quantize_score(sum(parts, ZERO) / len(parts))


@dataclass(frozen=True)
class SetupScore:
    scoring_version: str
    interpretation: str
    weights: dict[str, Any]
    components: dict[str, Decimal]
    deterministic_score: Decimal

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def score_setup(
    detection: PatternDetection,
    snapshot: FeatureSnapshot,
    direction: Direction,
    *,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
) -> SetupScore:
    """The composite, with every component kept beside it so any score can be taken apart afterwards."""
    components = {
        "pattern": detection.overall_score,
        "trend": trend_component(snapshot, direction),
        "volume": volume_component(snapshot),
        "pressure": pressure_component(snapshot, direction),
        "structure": structure_component(snapshot, direction),
    }
    with localcontext(CANONICAL_CONTEXT):
        total = sum((value * getattr(weights, name) for name, value in components.items()), ZERO)
    return SetupScore(
        scoring_version=SCORING_VERSION,
        interpretation=SCORE_INTERPRETATION,
        weights=weights.snapshot(),
        components=components,
        deterministic_score=quantize_score(total),
    )
