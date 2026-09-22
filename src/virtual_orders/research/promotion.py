"""The promotion boundary: the only place a research observation may turn into a platform signal (Plan 5, D86).

    Pattern detection -> Setup candidate -> Historical validation -> Model validation -> Paper signal
                                                                                            |
                                                                        existing virtual order engine

Research candidates are isolated by default. This module builds the body a promoted candidate *would* submit
and the list of reasons it may not be — and it does not submit anything. Submission stays an explicit, separate
call through the existing `/signals` intake (spec 3.3), so the platform's validation, idempotency and ordering
guarantees remain the authority. Nothing here weakens them:

* `client_signal_id` comes from the candidate and is stable across scans, so re-promoting the same occurrence
  is a no-op at the intake, not a second signal;
* the level chain was already validated by the platform's own `validate_signal`, and an invalid chain is a
  promotion error rather than something to repair here;
* `thesis` is the candidate's structured evidence serialized as canonical JSON — machine-readable, exactly as
  stored, and a plain string as the intake contract requires.

A gate is deliberately conservative: every requirement that is not met is reported, and an unproven candidate
is simply not promotable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any

from core.domain.hashing import canonical_json
from virtual_orders.research.backtest import BacktestStats
from virtual_orders.research.setups import RESEARCH_SOURCE, SetupCandidate

PROMOTION_VERSION = "promotion-v1"
DEFAULT_VALID_SESSIONS = 3

NOT_VALIDATED = "NOT_VALIDATED"
SCORE_BELOW_MINIMUM = "SCORE_BELOW_MINIMUM"
NO_LEVELS = "NO_LEVELS"
INVALID_LEVELS = "INVALID_LEVELS"
INSUFFICIENT_BACKTEST_SAMPLES = "INSUFFICIENT_BACKTEST_SAMPLES"
EXPECTANCY_BELOW_MINIMUM = "EXPECTANCY_BELOW_MINIMUM"
WIN_RATE_BELOW_MINIMUM = "WIN_RATE_BELOW_MINIMUM"
ML_PROBABILITY_REQUIRED = "ML_PROBABILITY_REQUIRED"
ML_PROBABILITY_BELOW_MINIMUM = "ML_PROBABILITY_BELOW_MINIMUM"
NO_TRADABLE_LEVELS = "NO_TRADABLE_LEVELS"

# A promotion the operator forced past this policy. It is a distinct `source` rather than a flag inside the
# thesis so that one SQL predicate separates what the engine decided from what a person overruled: the
# observation report, the metrics and every future read can tell the two apart without parsing JSON.
MANUAL_SOURCE = "manual_promotion"


@dataclass(frozen=True)
class PromotionPolicy:
    """What a candidate must clear before it may become a paper signal. Versioned with the promotion itself."""

    min_deterministic_score: Decimal = Decimal("0.60")
    min_backtest_samples: int = 30
    min_expectancy_r: Decimal = Decimal("0")
    min_win_rate: Decimal | None = None
    require_ml_probability: bool = False
    min_ml_probability: Decimal = Decimal("0.55")
    valid_sessions: int = DEFAULT_VALID_SESSIONS

    def __post_init__(self) -> None:
        if not 1 <= self.valid_sessions <= 20:
            raise ValueError("valid_sessions must be between 1 and 20")
        if not Decimal(0) <= self.min_deterministic_score <= Decimal(1):
            raise ValueError("min_deterministic_score must be in [0, 1]")
        if not Decimal(0) <= self.min_ml_probability <= Decimal(1):
            raise ValueError("min_ml_probability must be in [0, 1]")
        if self.min_backtest_samples < 1:
            raise ValueError("min_backtest_samples must be >= 1")

    def snapshot(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


DEFAULT_POLICY = PromotionPolicy()


def promotion_errors(
    candidate: SetupCandidate,
    *,
    evidence: BacktestStats | None = None,
    policy: PromotionPolicy = DEFAULT_POLICY,
) -> tuple[str, ...]:
    """Every reason this candidate may not become a signal, as fixed codes, in a fixed order.

    `evidence` is the out-of-sample backtest of this pattern and timeframe. Its absence is itself an error: a
    candidate that has never been measured historically is exactly what this boundary exists to stop.
    """
    errors: list[str] = []
    if candidate.deterministic_score < policy.min_deterministic_score:
        errors.append(SCORE_BELOW_MINIMUM)
    if candidate.levels is None:
        errors.append(NO_LEVELS)
    elif not candidate.levels.valid:
        errors.append(INVALID_LEVELS)
    if evidence is None:
        errors.append(NOT_VALIDATED)
    else:
        if evidence.samples < policy.min_backtest_samples:
            errors.append(INSUFFICIENT_BACKTEST_SAMPLES)
        if evidence.expectancy_r is None or evidence.expectancy_r <= policy.min_expectancy_r:
            errors.append(EXPECTANCY_BELOW_MINIMUM)
        if policy.min_win_rate is not None and (
            evidence.win_rate is None or evidence.win_rate < policy.min_win_rate
        ):
            errors.append(WIN_RATE_BELOW_MINIMUM)
    if policy.require_ml_probability:
        if candidate.ml_probability is None:
            errors.append(ML_PROBABILITY_REQUIRED)
        elif candidate.ml_probability < policy.min_ml_probability:
            errors.append(ML_PROBABILITY_BELOW_MINIMUM)
    return tuple(errors)


def is_promotable(
    candidate: SetupCandidate,
    *,
    evidence: BacktestStats | None = None,
    policy: PromotionPolicy = DEFAULT_POLICY,
) -> bool:
    return not promotion_errors(candidate, evidence=evidence, policy=policy)


@dataclass(frozen=True)
class PromotionDecision:
    promotion_version: str
    promotable: bool
    errors: tuple[str, ...]
    policy: dict[str, Any]
    client_signal_id: str
    body: dict[str, Any] | None

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def signal_body(candidate: SetupCandidate, *, policy: PromotionPolicy = DEFAULT_POLICY) -> dict[str, Any]:
    """The exact body the existing `POST /signals` intake expects. Raises rather than emit an unusable chain."""
    if candidate.levels is None or not candidate.levels.valid:
        raise ValueError("a candidate without a valid level chain has no signal body")
    levels = candidate.levels
    return {
        "client_signal_id": candidate.client_signal_id,
        "strategy": candidate.strategy,
        "strategy_version": candidate.strategy_version,
        "source": RESEARCH_SOURCE,
        "ticker": candidate.ticker,
        "direction": candidate.direction.value,
        "entry_zone_low": levels.entry_zone_low,
        "entry_zone_high": levels.entry_zone_high,
        "stop": levels.stop,
        "target1": levels.target1,
        "target2": levels.target2,
        "valid_sessions": policy.valid_sessions,
        # Documented interpretation travels with the number (spec 18): the deterministic composite, not a
        # probability. Any model probability rides inside the thesis, with its own versions beside it.
        "score": candidate.deterministic_score,
        "thesis": canonical_json(candidate.thesis()),
    }


def decide(
    candidate: SetupCandidate,
    *,
    evidence: BacktestStats | None = None,
    policy: PromotionPolicy = DEFAULT_POLICY,
) -> PromotionDecision:
    """Judge a candidate and, only when it passes, build its signal body. Never submits."""
    errors = promotion_errors(candidate, evidence=evidence, policy=policy)
    return PromotionDecision(
        promotion_version=PROMOTION_VERSION,
        promotable=not errors,
        errors=errors,
        policy=policy.snapshot(),
        client_signal_id=candidate.client_signal_id,
        body=None if errors else signal_body(candidate, policy=policy),
    )


def stored_promotion_errors(
    row: Mapping[str, Any],
    *,
    evidence: BacktestStats | None = None,
    policy: PromotionPolicy = DEFAULT_POLICY,
) -> tuple[str, ...]:
    """`promotion_errors` for a candidate read back from `setup_candidates`, in the same order and codes.

    A stored row carries the judged facts already — score, the level chain and its validity, any model
    probability — so judging it needs no reconstruction of the detection, the features or the score object
    that produced it. The two paths must agree: `tests/research/test_setups.py` pins that they do.
    """
    errors: list[str] = []
    score = Decimal(str(row["deterministic_score"]))
    if score < policy.min_deterministic_score:
        errors.append(SCORE_BELOW_MINIMUM)
    if row.get("entry_zone_low") is None:
        errors.append(NO_LEVELS)
    elif not row.get("levels_valid"):
        errors.append(INVALID_LEVELS)
    if evidence is None:
        errors.append(NOT_VALIDATED)
    else:
        if evidence.samples < policy.min_backtest_samples:
            errors.append(INSUFFICIENT_BACKTEST_SAMPLES)
        if evidence.expectancy_r is None or evidence.expectancy_r <= policy.min_expectancy_r:
            errors.append(EXPECTANCY_BELOW_MINIMUM)
        if policy.min_win_rate is not None and (
            evidence.win_rate is None or evidence.win_rate < policy.min_win_rate
        ):
            errors.append(WIN_RATE_BELOW_MINIMUM)
    if policy.require_ml_probability:
        probability = row.get("ml_probability")
        if probability is None:
            errors.append(ML_PROBABILITY_REQUIRED)
        elif Decimal(str(probability)) < policy.min_ml_probability:
            errors.append(ML_PROBABILITY_BELOW_MINIMUM)
    return tuple(errors)


def stored_signal_body(
    row: Mapping[str, Any],
    *,
    policy: PromotionPolicy = DEFAULT_POLICY,
    thesis: Mapping[str, Any] | None = None,
    overridden: tuple[str, ...] = (),
    auto_order: bool = True,
) -> dict[str, Any]:
    """The `POST /signals` body for a stored candidate. Raises rather than emit an untradable chain.

    An overridden promotion is marked twice over: `source` becomes `MANUAL_SOURCE`, and the errors that were
    overruled are written into the thesis under `promotion_override`. The prices are the candidate's own —
    an override waives the policy, never the level chain, because there is nothing to trade without one.
    """
    if row.get("entry_zone_low") is None or not row.get("levels_valid"):
        raise ValueError(NO_TRADABLE_LEVELS)
    document: dict[str, Any] = dict(thesis or {})
    if overridden:
        document["promotion_override"] = {
            "overridden_errors": list(overridden),
            "promotion_version": PROMOTION_VERSION,
            "policy": policy.snapshot(),
        }
    return {
        "client_signal_id": str(row["client_signal_id"]),
        "strategy": str(row["strategy"]),
        "strategy_version": str(row["strategy_version"]),
        "source": MANUAL_SOURCE if overridden else RESEARCH_SOURCE,
        "ticker": str(row["ticker"]),
        "direction": str(row["direction"]),
        "entry_zone_low": row["entry_zone_low"],
        "entry_zone_high": row["entry_zone_high"],
        "stop": row["stop"],
        "target1": row["target1"],
        "target2": row["target2"],
        "valid_sessions": policy.valid_sessions,
        "score": row["deterministic_score"],
        "thesis": canonical_json(document),
        "auto_order": auto_order,
    }


def decide_stored(
    row: Mapping[str, Any],
    *,
    evidence: BacktestStats | None = None,
    policy: PromotionPolicy = DEFAULT_POLICY,
) -> PromotionDecision:
    """Judge a stored candidate and, only when it passes, build its signal body. Never submits."""
    errors = stored_promotion_errors(row, evidence=evidence, policy=policy)
    return PromotionDecision(
        promotion_version=PROMOTION_VERSION,
        promotable=not errors,
        errors=errors,
        policy=policy.snapshot(),
        client_signal_id=str(row["client_signal_id"]),
        body=None if errors else stored_signal_body(row, policy=policy),
    )
