"""Research routes (Plan 5, D92): views over what the scan already recorded, plus one explicit promotion.

Every route here is a GET except `POST /research/candidates/{id}/promote`, which the owner asked for on
2026-09-22 so that a detected setup can be traded on paper from the dashboard. It remains an explicit
decision and never a side effect of looking at a list: promotion happens only on that POST, for one named
candidate, and the body it submits goes through the existing `POST /signals` intake with its own validation
and idempotency. Starting a scan is still not an HTTP operation — a scan reads many sessions of stored bars
and belongs to the worker.

The promotion boundary itself is unchanged. `promotion.decide_stored` judges the candidate against the same
policy as before and refuses it by default. `override=true` waives that policy on the operator's authority,
and such a signal is recorded under a distinct `source` so that what a person overruled can always be told
apart from what the engine decided. An override never waives the level chain: a candidate without valid
levels has no prices to trade and is refused whatever the flag says.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Body, Query, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.api.errors import ApiError
from virtual_orders.api.intake import intake_signal
from virtual_orders.api.tickers import normalize_ticker
from virtual_orders.evaluator.signals import SignalValidationError
from virtual_orders.readmodels.research import (
    candidate_detail,
    latest_backtest_stats,
    list_backtests,
    list_candidates,
    list_detections,
    list_registered_models,
    list_runs,
    pattern_markers,
)
from virtual_orders.research.candlesticks import ENGINE_VERSION, SUPPORTED_PATTERNS
from virtual_orders.research.features import FEATURE_VERSION
from virtual_orders.research.labels import AMBIGUITY_POLICY, LABEL_VERSION
from virtual_orders.research.models import Timeframe
from virtual_orders.research.promotion import (
    NO_TRADABLE_LEVELS,
    decide_stored,
    stored_signal_body,
)
from virtual_orders.research.repository import ResearchRunKind
from virtual_orders.research.scoring import SCORE_INTERPRETATION, SCORING_VERSION
from virtual_orders.research.service import DEFAULT_TIMEFRAMES

router = APIRouter()
RESEARCH_REQUEST_INVALID = "RESEARCH_REQUEST_INVALID"
CANDIDATE_NOT_PROMOTABLE = "CANDIDATE_NOT_PROMOTABLE"
RESEARCH_CANDIDATE_NOT_FOUND = "RESEARCH_CANDIDATE_NOT_FOUND"
MAX_WINDOW_DAYS = 400


def _invalid(errors: list[str]) -> ApiError:
    return ApiError(422, RESEARCH_REQUEST_INVALID, detail={"errors": errors})


def _timeframe(value: str | None) -> Timeframe | None:
    if value is None:
        return None
    try:
        return Timeframe(value)
    except ValueError:
        raise _invalid([f"INVALID_CHOICE:timeframe:{value}"]) from None


def _pattern(value: str | None) -> str | None:
    if value is None:
        return None
    if value not in SUPPORTED_PATTERNS:
        raise _invalid([f"INVALID_CHOICE:pattern:{value}"])
    return value


def _direction(value: str | None, allowed: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise _invalid([f"INVALID_CHOICE:direction:{value}"])
    return value


def _aware(name: str, value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise _invalid([f"NAIVE_DATETIME:{name}"])
    return value


@router.get("/research/versions")
def get_research_versions(services: ServicesDep) -> Response:
    """The rule versions behind every stored observation, and what the deterministic score does and does not mean."""
    return json_response({
        "engine_version": ENGINE_VERSION,
        "feature_version": FEATURE_VERSION,
        "scoring_version": SCORING_VERSION,
        "label_version": LABEL_VERSION,
        "ambiguity_policy": AMBIGUITY_POLICY,
        "score_interpretation": SCORE_INTERPRETATION,
        "supported_patterns": list(SUPPORTED_PATTERNS),
        "supported_timeframes": [item.value for item in Timeframe],
        "scanned_timeframes": [item.value for item in DEFAULT_TIMEFRAMES],
        "code_version": services.code_version,
    })


@router.get("/research/runs")
def get_research_runs(
    services: ServicesDep,
    kind: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> Response:
    if kind is not None and kind not in tuple(item.value for item in ResearchRunKind):
        raise _invalid([f"INVALID_CHOICE:kind:{kind}"])
    with services.engine.connect() as conn:
        return json_response({"runs": list_runs(conn, kind=kind, limit=limit)})


@router.get("/research/detections")
def get_research_detections(
    services: ServicesDep,
    ticker: str | None = None,
    timeframe: str | None = None,
    pattern: str | None = None,
    direction: str | None = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    with services.engine.connect() as conn:
        rows = list_detections(
            conn, ticker=None if ticker is None else normalize_ticker(ticker), timeframe=_timeframe(timeframe),
            pattern=_pattern(pattern), direction=_direction(direction, ("BULLISH", "BEARISH", "NEUTRAL")),
            start=_aware("from", start), end=_aware("to", end), limit=limit, offset=offset,
        )
    return json_response({"detections": rows, "engine_version": ENGINE_VERSION})


@router.get("/research/candidates")
def get_research_candidates(
    services: ServicesDep,
    ticker: str | None = None,
    timeframe: str | None = None,
    pattern: str | None = None,
    direction: str | None = None,
    min_score: Decimal | None = None,
    levels_valid: bool | None = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    """The scanner (spec 21). `score_interpretation` travels with the rows so the number is never read bare."""
    if min_score is not None and not (min_score.is_finite() and 0 <= min_score <= 1):
        raise _invalid(["OUT_OF_RANGE:min_score"])
    with services.engine.connect() as conn:
        rows = list_candidates(
            conn, ticker=None if ticker is None else normalize_ticker(ticker), timeframe=_timeframe(timeframe),
            pattern=_pattern(pattern), direction=_direction(direction, ("LONG", "SHORT")), min_score=min_score,
            levels_valid=levels_valid, start=_aware("from", start), end=_aware("to", end), limit=limit,
            offset=offset,
        )
    return json_response({
        "candidates": rows, "score_interpretation": SCORE_INTERPRETATION, "feature_version": FEATURE_VERSION,
        "scoring_version": SCORING_VERSION,
    })


@router.get("/research/candidates/{candidate_id}")
def get_research_candidate(candidate_id: int, services: ServicesDep) -> Response:
    with services.engine.connect() as conn:
        found = candidate_detail(conn, candidate_id)
    if found is None:
        raise ApiError(404, "RESEARCH_CANDIDATE_NOT_FOUND")
    return json_response({**found, "score_interpretation": SCORE_INTERPRETATION})


@router.get("/research/markers")
def get_research_markers(
    services: ServicesDep,
    ticker: str,
    timeframe: str,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> Response:
    """Detected patterns as chart markers, in the platform's existing marker shape (spec 22)."""
    errors = [f"NAIVE_DATETIME:{name}" for name, value in (("from", start), ("to", end)) if value.tzinfo is None]
    if not errors and end <= start:
        errors.append("EMPTY_WINDOW")
    if errors:
        raise _invalid(errors)
    resolved = _timeframe(timeframe)
    assert resolved is not None  # `timeframe` is required here, so _timeframe never returns None
    with services.engine.connect() as conn:
        markers = pattern_markers(conn, ticker=normalize_ticker(ticker), timeframe=resolved, start=start,
                                  end=end, limit=limit)
    return json_response({"markers": markers, "ticker": normalize_ticker(ticker), "timeframe": resolved.value})


@router.get("/research/backtests")
def get_research_backtests(
    services: ServicesDep,
    pattern: str | None = None,
    timeframe: str | None = None,
    split: str | None = None,
    ticker: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Response:
    """Historical statistics. Every row carries its sample count: a rate is never published without one."""
    if split is not None and split not in ("ALL", "TRAIN", "VALIDATION", "TEST", "FOLD"):
        raise _invalid([f"INVALID_CHOICE:split:{split}"])
    with services.engine.connect() as conn:
        rows = list_backtests(
            conn, pattern=_pattern(pattern), timeframe=_timeframe(timeframe), split=split,
            ticker=None if ticker is None else normalize_ticker(ticker), limit=limit,
        )
    return json_response({"backtests": rows, "label_version": LABEL_VERSION,
                          "ambiguity_policy": AMBIGUITY_POLICY})


@router.get("/research/models")
def get_research_models(
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    with services.engine.connect() as conn:
        return json_response({"models": list_registered_models(conn, limit=limit),
                              "feature_version": FEATURE_VERSION})


@router.post("/research/candidates/{candidate_id}/promote")
def post_promote_candidate(
    candidate_id: int,
    services: ServicesDep,
    override: Annotated[bool, Body(embed=True)] = False,
    auto_order: Annotated[bool, Body(embed=True)] = True,
) -> Response:
    """Promote one stored candidate into a paper signal through the existing intake.

    The decision is always computed and always returned, whether it allowed the promotion or was overridden,
    so the caller sees the policy it passed or overruled rather than a bare success.
    """
    with services.engine.connect() as conn:
        found = candidate_detail(conn, candidate_id)
        if found is None:
            raise ApiError(404, RESEARCH_CANDIDATE_NOT_FOUND)
        candidate = found["candidate"]
        evidence = latest_backtest_stats(
            conn, pattern=str(candidate["pattern"]), timeframe=str(candidate["timeframe"])
        )
    decision = decide_stored(candidate, evidence=evidence)
    if not decision.promotable and not override:
        raise ApiError(409, CANDIDATE_NOT_PROMOTABLE, detail={"errors": list(decision.errors),
                                                             "decision": decision.as_document()})
    try:
        body = (
            decision.body if decision.promotable
            else stored_signal_body(candidate, thesis=found.get("thesis_document"),
                                    overridden=decision.errors, auto_order=auto_order)
        )
    except ValueError:  # no level chain: an override waives the policy, never the prices
        raise ApiError(409, CANDIDATE_NOT_PROMOTABLE,
                       detail={"errors": [NO_TRADABLE_LEVELS], "decision": decision.as_document()}) from None
    assert body is not None
    try:
        submitted = intake_signal(services, {**body, "auto_order": auto_order})
    except SignalValidationError as exc:
        raise ApiError(422, RESEARCH_REQUEST_INVALID, detail={"errors": exc.errors}) from None
    return json_response({
        "candidate_id": candidate_id,
        "signal_id": submitted.signal_id,
        "auto_order_id": submitted.auto_order_id,
        "status": submitted.status,
        "overridden": not decision.promotable,
        "decision": decision.as_document(),
    }, 201)
