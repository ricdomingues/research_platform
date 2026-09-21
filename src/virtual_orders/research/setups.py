"""The setup layer: a pattern only becomes a candidate with context behind it (Plan 5, D83).

    pattern + technical context + market structure + volume/pressure  ->  SetupCandidate

A `SetupCandidate` is a **research observation**. Nothing here submits anything: it carries no side effects, and
the promotion boundary that could one day turn a validated candidate into a paper signal lives behind its own
explicit step (`promotion.py`), never inside detection. That separation is the whole point of the layer — a
detected pattern is not a recommendation, and this module is where that stops being an opinion and becomes a
type.

The `client_signal_id` is deterministic so a promoted candidate is idempotent under the existing intake
contract (spec 3.3): re-running the same scan can never create a second signal for the same pattern occurrence.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from typing import Any

from core.domain.hashing import sha256_hex
from core.domain.models import Direction
from virtual_orders.research.features import FeatureSnapshot
from virtual_orders.research.levels import RiskLevels
from virtual_orders.research.models import PatternDetection, PatternDirection, Timeframe
from virtual_orders.research.scoring import SetupScore

RESEARCH_STRATEGY = "CANDLE_RESEARCH"
RESEARCH_SOURCE = "research_scan"


def strategy_version(engine_version: str, feature_version: str, scoring_version: str) -> str:
    """One version string covering every rule that produced a candidate: rules change, the version changes."""
    return f"{engine_version}+{feature_version}+{scoring_version}"


def direction_of(direction: PatternDirection) -> Direction | None:
    if direction is PatternDirection.BULLISH:
        return Direction.LONG
    if direction is PatternDirection.BEARISH:
        return Direction.SHORT
    return None


@dataclass(frozen=True)
class SetupCandidate:
    """A research observation. `ml_probability` stays None until an approved model has scored it."""

    ticker: str
    timeframe: Timeframe
    detected_at: datetime
    pattern_start_ts: datetime
    direction: Direction
    pattern: str
    strategy: str
    strategy_version: str
    feature_version: str
    engine_version: str
    scoring_version: str
    deterministic_score: Decimal
    data_as_of: datetime
    detection: PatternDetection
    features: FeatureSnapshot
    score: SetupScore
    levels: RiskLevels | None
    ml_probability: Decimal | None = None
    model_version: str | None = None
    label_version: str | None = None

    @property
    def client_signal_id(self) -> str:
        """`strategy-version:ticker:timeframe:pattern:direction:pattern_end_ts` — one id per occurrence.

        `pattern` and `direction` belong in the id because one bar can satisfy more than one detector: the
        canary produced BEARISH_ENGULFING and EVENING_STAR on the same AAPL 1h candle. Without them every
        detection on that bar shares a single id, and the intake contract's uniqueness on `client_signal_id`
        keeps whichever arrived first. For two same-direction readings that collapse is defensible; for two
        families disagreeing on direction it means the surviving side is chosen by iteration order rather
        than by any rule, which is not a decision this layer is allowed to make silently.

        The scan's `data_as_of` is deliberately **not** part of the id, though it is recorded in the payload:
        every scan takes a fresh watermark, so folding it in would mint a new signal for the same pattern on
        every run and defeat the idempotency the intake contract depends on (spec 3.3).
        """
        return (f"{self.strategy}-{self.strategy_version}:{self.ticker}:{self.timeframe.value}:"
                f"{self.pattern}:{self.direction.value}:{self.detected_at.isoformat()}")

    @property
    def candidate_key(self) -> str:
        """Identity of a candidate inside the research tables, independent of when the scan ran."""
        return (f"{self.strategy_version}:{self.ticker}:{self.timeframe.value}:{self.pattern}:"
                f"{self.detected_at.isoformat()}")

    @property
    def candidate_hash(self) -> str:
        """Identity of *what* was observed: a corrected bar that changes the reading changes this hash."""
        return sha256_hex({
            "candidate_key": self.candidate_key, "detection": self.detection.evidence_hash,
            "features": self.features.feature_hash, "deterministic_score": self.deterministic_score,
            "levels": None if self.levels is None else self.levels.as_document(),
        })

    def thesis(self) -> dict[str, Any]:
        """Structured, machine-readable evidence (spec 18). Deliberately not prose: every field is checkable."""
        document: dict[str, Any] = {
            "kind": "RESEARCH_CANDLESTICK_SETUP",
            "pattern": self.pattern,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "detected_at": self.detected_at,
            "engine_version": self.engine_version,
            "feature_version": self.feature_version,
            "scoring_version": self.scoring_version,
            "deterministic_score": self.deterministic_score,
            "score_interpretation": self.score.interpretation,
            "score_components": self.score.components,
            "pattern_evidence": self.detection.evidence,
            "context": {
                "short_term_trend": self.features.short_term_trend,
                "medium_term_trend": self.features.medium_term_trend,
                "breakout": self.features.breakout,
                "rsi14": self.features.rsi14,
                "atr14": self.features.atr14,
                "relative_volume": self.features.relative_volume,
                "support_distance_pct": self.features.support_distance_pct,
                "resistance_distance_pct": self.features.resistance_distance_pct,
            },
            "pressure_estimate": {
                "method": self.features.pressure_method,
                "disclaimer": self.features.pressure_disclaimer,
                "cmf": self.features.cmf,
                "obv_slope": self.features.obv_slope,
                "vwap_distance_pct": self.features.vwap_distance_pct,
                "side": self.features.pressure_side,
            },
            "data_as_of": self.data_as_of,
            "feature_hash": self.features.feature_hash,
        }
        if self.levels is not None:
            document["levels"] = {
                "entry_zone_low": self.levels.entry_zone_low, "entry_zone_high": self.levels.entry_zone_high,
                "stop": self.levels.stop, "target1": self.levels.target1, "target2": self.levels.target2,
                "risk_reward": self.levels.risk_reward, "levels_version": self.levels.levels_version,
                "basis": self.levels.basis, "valid": self.levels.valid, "errors": list(self.levels.errors),
            }
        if self.ml_probability is not None:
            document["ml"] = {
                "probability_target_first": self.ml_probability, "model_version": self.model_version,
                "feature_version": self.feature_version, "label_version": self.label_version,
            }
        return document

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def with_prediction(
        self, *, probability: Decimal, model_version: str, label_version: str
    ) -> SetupCandidate:
        """A scored copy. The deterministic candidate is never mutated: a model observes, it does not rewrite."""
        return SetupCandidate(
            **{**{item.name: getattr(self, item.name) for item in fields(self)},
               "ml_probability": probability, "model_version": model_version, "label_version": label_version},
        )


def build_candidate(
    *,
    ticker: str,
    detection: PatternDetection,
    snapshot: FeatureSnapshot,
    score: SetupScore,
    data_as_of: datetime,
    levels: RiskLevels | None = None,
) -> SetupCandidate | None:
    """Assemble a candidate, or None when the pattern carries no tradable direction (a plain Doji)."""
    direction = direction_of(detection.direction)
    if direction is None:
        return None
    version = strategy_version(detection.engine_version, snapshot.feature_version, score.scoring_version)
    return SetupCandidate(
        ticker=ticker,
        timeframe=detection.timeframe,
        detected_at=detection.end_ts,
        pattern_start_ts=detection.start_ts,
        direction=direction,
        pattern=detection.pattern,
        strategy=RESEARCH_STRATEGY,
        strategy_version=version,
        feature_version=snapshot.feature_version,
        engine_version=detection.engine_version,
        scoring_version=score.scoring_version,
        deterministic_score=score.deterministic_score,
        data_as_of=data_as_of,
        detection=detection,
        features=snapshot,
        score=score,
        levels=levels,
    )
