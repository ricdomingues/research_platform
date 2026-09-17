"""Read models for the research domain (Plan 5, D92). Reads only: nothing here scans, scores or promotes.

The scanner row is assembled from the candidate's stored `feature_document` rather than recomputed, so what the
dashboard shows is exactly what the scan recorded at its watermark — the same number, months later, whatever
the vendor has since corrected.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Select, and_, select

from virtual_orders.research.models import Timeframe
from virtual_orders.storage.tables import (
    pattern_detections,
    research_backtests,
    research_models,
    research_runs,
    setup_candidates,
)

SCANNER_FEATURES = (
    "short_term_trend", "medium_term_trend", "rsi14", "relative_volume", "pressure_side", "cmf",
    "vwap_distance_pct", "support_distance_pct", "resistance_distance_pct", "atr14", "breakout",
)
DETECTION_COLUMNS = (
    pattern_detections.c.id, pattern_detections.c.run_id, pattern_detections.c.ticker,
    pattern_detections.c.timeframe, pattern_detections.c.pattern, pattern_detections.c.direction,
    pattern_detections.c.start_ts, pattern_detections.c.end_ts, pattern_detections.c.candles,
    pattern_detections.c.geometry_score, pattern_detections.c.context_score, pattern_detections.c.overall_score,
    pattern_detections.c.engine_version, pattern_detections.c.price_source, pattern_detections.c.evidence,
    pattern_detections.c.data_as_of, pattern_detections.c.created_at,
)
CANDIDATE_COLUMNS = (
    setup_candidates.c.id, setup_candidates.c.run_id, setup_candidates.c.pattern_detection_id,
    setup_candidates.c.ticker, setup_candidates.c.timeframe, setup_candidates.c.detected_at,
    setup_candidates.c.direction, setup_candidates.c.pattern, setup_candidates.c.strategy,
    setup_candidates.c.strategy_version, setup_candidates.c.feature_version, setup_candidates.c.scoring_version,
    setup_candidates.c.deterministic_score, setup_candidates.c.ml_probability, setup_candidates.c.model_version,
    setup_candidates.c.label_version, setup_candidates.c.entry_zone_low, setup_candidates.c.entry_zone_high,
    setup_candidates.c.stop, setup_candidates.c.target1, setup_candidates.c.target2,
    setup_candidates.c.risk_reward, setup_candidates.c.levels_valid, setup_candidates.c.levels_errors,
    setup_candidates.c.client_signal_id, setup_candidates.c.data_as_of, setup_candidates.c.created_at,
)


def _window(query: Select[Any], column: Any, start: datetime | None, end: datetime | None) -> Select[Any]:
    if start is not None:
        query = query.where(column >= start)
    if end is not None:
        query = query.where(column < end)
    return query


def list_runs(conn: Connection, *, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    query = select(research_runs).order_by(research_runs.c.started_at.desc(), research_runs.c.run_id).limit(limit)
    if kind is not None:
        query = query.where(research_runs.c.kind == kind)
    return [dict(row) for row in conn.execute(query).mappings()]


def list_detections(
    conn: Connection,
    *,
    ticker: str | None = None,
    timeframe: Timeframe | None = None,
    pattern: str | None = None,
    direction: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    query = select(*DETECTION_COLUMNS)
    if ticker is not None:
        query = query.where(pattern_detections.c.ticker == ticker)
    if timeframe is not None:
        query = query.where(pattern_detections.c.timeframe == timeframe.value)
    if pattern is not None:
        query = query.where(pattern_detections.c.pattern == pattern)
    if direction is not None:
        query = query.where(pattern_detections.c.direction == direction)
    query = _window(query, pattern_detections.c.end_ts, start, end)
    query = query.order_by(pattern_detections.c.end_ts.desc(), pattern_detections.c.id.desc())
    return [dict(row) for row in conn.execute(query.limit(limit).offset(offset)).mappings()]


def _scanner_features(document: dict[str, Any]) -> dict[str, Any]:
    return {name: document.get(name) for name in SCANNER_FEATURES}


def list_candidates(
    conn: Connection,
    *,
    ticker: str | None = None,
    timeframe: Timeframe | None = None,
    pattern: str | None = None,
    direction: str | None = None,
    min_score: Decimal | None = None,
    levels_valid: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The scanner: one row per candidate, with the context values it was scored on (spec 21)."""
    query = select(*CANDIDATE_COLUMNS, setup_candidates.c.feature_document)
    if ticker is not None:
        query = query.where(setup_candidates.c.ticker == ticker)
    if timeframe is not None:
        query = query.where(setup_candidates.c.timeframe == timeframe.value)
    if pattern is not None:
        query = query.where(setup_candidates.c.pattern == pattern)
    if direction is not None:
        query = query.where(setup_candidates.c.direction == direction)
    if min_score is not None:
        query = query.where(setup_candidates.c.deterministic_score >= min_score)
    if levels_valid is not None:
        query = query.where(setup_candidates.c.levels_valid.is_(levels_valid))
    query = _window(query, setup_candidates.c.detected_at, start, end)
    query = query.order_by(setup_candidates.c.detected_at.desc(), setup_candidates.c.id.desc())
    rows = []
    for row in conn.execute(query.limit(limit).offset(offset)).mappings():
        item = dict(row)
        document = dict(item.pop("feature_document"))
        rows.append({**item, "features": _scanner_features(document)})
    return rows


def candidate_detail(conn: Connection, candidate_id: int) -> dict[str, Any] | None:
    """One candidate with its whole feature snapshot, structured thesis and originating detection."""
    row = conn.execute(
        select(*CANDIDATE_COLUMNS, setup_candidates.c.feature_document, setup_candidates.c.thesis_document,
               setup_candidates.c.candidate_hash)
        .where(setup_candidates.c.id == candidate_id)
    ).mappings().first()
    if row is None:
        return None
    candidate = dict(row)
    detection = conn.execute(
        select(*DETECTION_COLUMNS).where(pattern_detections.c.id == candidate["pattern_detection_id"])
    ).mappings().first()
    return {"candidate": candidate, "detection": None if detection is None else dict(detection)}


def pattern_markers(
    conn: Connection,
    *,
    ticker: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Chart markers in the platform's existing marker shape (type, ts, price), plus the research fields.

    The price is the candidate's entry zone when it has valid levels; without one the marker has no price and
    the existing chart builder draws it as a vertical line, exactly as it does for a DATA_GAP.
    """
    query = (
        select(
            pattern_detections.c.id, pattern_detections.c.pattern, pattern_detections.c.direction,
            pattern_detections.c.end_ts, pattern_detections.c.overall_score, pattern_detections.c.timeframe,
            setup_candidates.c.entry_zone_high, setup_candidates.c.entry_zone_low,
            setup_candidates.c.deterministic_score, setup_candidates.c.ml_probability,
            setup_candidates.c.id.label("candidate_id"),
        )
        .select_from(pattern_detections.outerjoin(
            setup_candidates, setup_candidates.c.pattern_detection_id == pattern_detections.c.id
        ))
        .where(and_(
            pattern_detections.c.ticker == ticker,
            pattern_detections.c.timeframe == timeframe.value,
            pattern_detections.c.end_ts >= start,
            pattern_detections.c.end_ts < end,
        ))
        .order_by(pattern_detections.c.end_ts, pattern_detections.c.id)
        .limit(limit)
    )
    markers: list[dict[str, Any]] = []
    for row in conn.execute(query).mappings():
        zone_high, zone_low = row["entry_zone_high"], row["entry_zone_low"]
        price = zone_high if row["direction"] == "BULLISH" else zone_low
        markers.append({
            "seq": row["id"], "type": row["pattern"], "event_key": f"{row['pattern']}:{row['end_ts'].isoformat()}",
            "ts": row["end_ts"], "price": price, "pattern": row["pattern"], "direction": row["direction"],
            "timeframe": row["timeframe"], "pattern_score": row["overall_score"],
            "deterministic_score": row["deterministic_score"], "ml_probability": row["ml_probability"],
            "candidate_id": row["candidate_id"],
        })
    return markers


def list_backtests(
    conn: Connection,
    *,
    pattern: str | None = None,
    timeframe: Timeframe | None = None,
    split: str | None = None,
    ticker: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query = select(research_backtests)
    if pattern is not None:
        query = query.where(research_backtests.c.pattern == pattern)
    if timeframe is not None:
        query = query.where(research_backtests.c.timeframe == timeframe.value)
    if split is not None:
        query = query.where(research_backtests.c.split == split)
    if ticker is not None:
        query = query.where(research_backtests.c.ticker == ticker)
    query = query.order_by(research_backtests.c.created_at.desc(), research_backtests.c.id.desc())
    return [dict(row) for row in conn.execute(query.limit(limit)).mappings()]


def list_registered_models(conn: Connection, *, limit: int = 20) -> list[dict[str, Any]]:
    """Registry rows without their artifacts: a listing names models, it does not ship ensembles over HTTP."""
    columns = (
        research_models.c.model_name, research_models.c.model_version, research_models.c.model_kind,
        research_models.c.framework, research_models.c.feature_version, research_models.c.label_version,
        research_models.c.training_from, research_models.c.training_to, research_models.c.training_rows,
        research_models.c.training_metrics, research_models.c.validation_metrics,
        research_models.c.artifact_hash, research_models.c.created_at,
    )
    query = select(*columns).order_by(research_models.c.created_at.desc(), research_models.c.id.desc())
    return [dict(row) for row in conn.execute(query.limit(limit)).mappings()]
