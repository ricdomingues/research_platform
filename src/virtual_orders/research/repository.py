"""Persistence for research observations (Plan 5, D89).

The database, not application care, is what makes a scan idempotent. `pattern_detections` and
`setup_candidates` each carry a unique key over (versioned identity + content hash), so re-running the same
scan at the same watermark inserts nothing, while a vendor correction that genuinely changes a reading lands
beside the original instead of overwriting it. Every write here is `ON CONFLICT DO NOTHING` followed by a read
of the existing row, so a repeated run is a no-op that still returns the ids a caller needs.

`research_runs` is the one mutable table of the domain — a control record that opens RUNNING and closes
COMPLETED or FAILED. Everything it points at is append-only.

Float values (bootstrap intervals, drawdowns) are converted to Decimal here, at the storage boundary. The
platform's canonical serializer refuses floats outright, which is the behaviour that catches them: rather than
weaken it, this module does the conversion in one place and documents it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.domain.hashing import sha256_hex
from virtual_orders.research.backtest import BacktestStats
from virtual_orders.research.labels import AMBIGUITY_POLICY, LABEL_VERSION
from virtual_orders.research.models import PatternDetection, Timeframe
from virtual_orders.research.promotion import NO_LEVELS
from virtual_orders.research.setups import SetupCandidate
from virtual_orders.storage.codec import to_document
from virtual_orders.storage.tables import (
    pattern_detections,
    research_backtests,
    research_runs,
    research_supersessions,
    setup_candidates,
)

# Supersession vocabulary (Plan 6, D96): the two ways a later revision can retire a fact, and the two kinds of
# fact it can retire.
SUPERSEDED_BY_REVISION = "SUPERSEDED_BY_REVISION"
NO_LONGER_DETECTED = "NO_LONGER_DETECTED"
FACT_PATTERN_DETECTION = "PATTERN_DETECTION"
FACT_SETUP_CANDIDATE = "SETUP_CANDIDATE"


class ResearchRunKind(StrEnum):
    RESEARCH_SCAN = "RESEARCH_SCAN"
    BACKTEST = "BACKTEST"
    TRAINING = "TRAINING"


class ResearchRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def json_safe(value: Any) -> Any:
    """Convert floats to Decimal so a document can pass the canonical serializer.

    A non-finite float becomes None: `nan` and `inf` have no canonical representation, and silently storing a
    string like "nan" in a numeric field would be worse than recording that the statistic was undefined.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return None if not math.isfinite(value) else Decimal(repr(value))
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    return value


def document(value: Any) -> dict[str, Any]:
    """A JSONB-ready document: floats converted, then canonically normalized like every other stored payload."""
    result: dict[str, Any] = to_document(json_safe(value))
    return result


def config_hash(configuration: Mapping[str, Any]) -> str:
    """Identity of the rules a run applied: thresholds, weights, policies and versions."""
    return sha256_hex(json_safe(dict(configuration)))


@dataclass(frozen=True)
class ResearchRun:
    run_id: UUID
    kind: ResearchRunKind
    data_as_of: datetime
    code_version: str
    engine_version: str
    config_hash: str
    started_at: datetime


def start_run(
    conn: Connection,
    *,
    kind: ResearchRunKind,
    data_as_of: datetime,
    code_version: str,
    engine_version: str,
    configuration: Mapping[str, Any],
    started_at: datetime | None = None,
    detail: Mapping[str, Any] | None = None,
) -> ResearchRun:
    started: datetime = started_at or conn.execute(select(func.clock_timestamp())).scalar_one()
    digest = config_hash(configuration)
    run = ResearchRun(uuid4(), kind, data_as_of, code_version, engine_version, digest, started)
    conn.execute(research_runs.insert().values(
        run_id=run.run_id, kind=kind.value, data_as_of=data_as_of, code_version=code_version,
        engine_version=engine_version, config_hash=digest, status=ResearchRunStatus.RUNNING.value,
        started_at=started, completed_at=None,
        detail=document({"configuration": dict(configuration), **dict(detail or {})}),
    ))
    return run


def finish_run(
    conn: Connection,
    run_id: UUID,
    status: ResearchRunStatus,
    detail: Mapping[str, Any] | None = None,
    *,
    completed_at: datetime | None = None,
) -> None:
    """Close the control record. The research facts it points at are already durable and are never revisited."""
    existing = conn.execute(select(research_runs.c.detail).where(research_runs.c.run_id == run_id)).scalar_one()
    conn.execute(
        update(research_runs)
        .where(research_runs.c.run_id == run_id)
        .values(status=status.value, completed_at=completed_at or func.clock_timestamp(),
                detail=document({**dict(existing), **dict(detail or {})}))
    )


def record_detection(
    conn: Connection,
    *,
    run_id: UUID,
    ticker: str,
    price_source: str,
    detection: PatternDetection,
    data_as_of: datetime,
    input_content_hash: str | None = None,
) -> tuple[int, bool]:
    """Store one detection idempotently. Returns (id, inserted): a repeat scan returns the original id."""
    values = {
        "run_id": run_id, "ticker": ticker, "timeframe": detection.timeframe.value, "pattern": detection.pattern,
        "direction": detection.direction.value, "start_ts": detection.start_ts, "end_ts": detection.end_ts,
        "candles": detection.candles, "geometry_score": detection.geometry_score,
        "context_score": detection.context_score, "overall_score": detection.overall_score,
        "engine_version": detection.engine_version, "price_source": price_source,
        "evidence": document(detection.evidence), "evidence_hash": detection.evidence_hash,
        "input_content_hash": input_content_hash,
        "data_as_of": data_as_of,
    }
    statement = (
        pg_insert(pattern_detections)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["engine_version", "ticker", "timeframe", "pattern", "end_ts",
                                                "evidence_hash"])
        .returning(pattern_detections.c.id)
    )
    inserted = conn.execute(statement).scalar_one_or_none()
    if inserted is not None:
        return int(inserted), True
    existing = conn.execute(
        select(pattern_detections.c.id).where(and_(
            pattern_detections.c.engine_version == detection.engine_version,
            pattern_detections.c.ticker == ticker,
            pattern_detections.c.timeframe == detection.timeframe.value,
            pattern_detections.c.pattern == detection.pattern,
            pattern_detections.c.end_ts == detection.end_ts,
            pattern_detections.c.evidence_hash == detection.evidence_hash,
        ))
    ).scalar_one()
    return int(existing), False


def record_candidate(
    conn: Connection, *, run_id: UUID, detection_id: int, candidate: SetupCandidate,
    input_content_hash: str | None = None,
) -> tuple[int, bool]:
    """Store one setup candidate idempotently, with its whole feature snapshot and structured thesis."""
    levels = candidate.levels
    values: dict[str, Any] = {
        "run_id": run_id, "pattern_detection_id": detection_id, "ticker": candidate.ticker,
        "timeframe": candidate.timeframe.value, "detected_at": candidate.detected_at,
        "direction": candidate.direction.value, "pattern": candidate.pattern, "strategy": candidate.strategy,
        "strategy_version": candidate.strategy_version, "feature_version": candidate.feature_version,
        "scoring_version": candidate.scoring_version,
        "feature_document": document(candidate.features.as_document()),
        "thesis_document": document(candidate.thesis()),
        "deterministic_score": candidate.deterministic_score, "ml_probability": candidate.ml_probability,
        "model_version": candidate.model_version, "label_version": candidate.label_version,
        "entry_zone_low": None if levels is None else levels.entry_zone_low,
        "entry_zone_high": None if levels is None else levels.entry_zone_high,
        "stop": None if levels is None else levels.stop,
        "target1": None if levels is None else levels.target1,
        "target2": None if levels is None else levels.target2,
        "risk_reward": None if levels is None else levels.risk_reward,
        "levels_valid": bool(levels is not None and levels.valid),
        # A candidate with no level chain at all is rejected for a reason, and the reason is the row's job to
        # carry. Storing an empty list here made those rejections reasonless in the fact table: `promotion.py`
        # knows the candidate failed on NO_LEVELS, but nothing that reads `setup_candidates` ever saw it.
        "levels_errors": [NO_LEVELS] if levels is None else list(levels.errors),
        "client_signal_id": candidate.client_signal_id, "candidate_hash": candidate.candidate_hash,
        "input_content_hash": input_content_hash,
        "data_as_of": candidate.data_as_of,
    }
    statement = (
        pg_insert(setup_candidates)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["strategy_version", "ticker", "timeframe", "pattern",
                                                "detected_at", "candidate_hash"])
        .returning(setup_candidates.c.id)
    )
    inserted = conn.execute(statement).scalar_one_or_none()
    if inserted is not None:
        return int(inserted), True
    existing = conn.execute(
        select(setup_candidates.c.id).where(and_(
            setup_candidates.c.strategy_version == candidate.strategy_version,
            setup_candidates.c.ticker == candidate.ticker,
            setup_candidates.c.timeframe == candidate.timeframe.value,
            setup_candidates.c.pattern == candidate.pattern,
            setup_candidates.c.detected_at == candidate.detected_at,
            setup_candidates.c.candidate_hash == candidate.candidate_hash,
        ))
    ).scalar_one()
    return int(existing), False


def record_backtest(
    conn: Connection,
    *,
    run_id: UUID,
    stats: BacktestStats,
    pattern: str,
    timeframe: Timeframe,
    split: str,
    period_from: datetime,
    period_to: datetime,
    data_as_of: datetime,
    ticker: str | None = None,
    fold: int | None = None,
) -> int:
    """Store one aggregate result. Floats (drawdown, intervals) become Decimal on the way in."""
    drawdown = json_safe(stats.max_drawdown_r)
    row = conn.execute(research_backtests.insert().values(
        run_id=run_id, ticker=ticker, timeframe=timeframe.value, pattern=pattern, split=split, fold=fold,
        period_from=period_from, period_to=period_to, samples=stats.samples, resolved=stats.resolved,
        wins=stats.wins, losses=stats.losses, timeouts=stats.timeouts, ambiguous=stats.ambiguous,
        win_rate=stats.win_rate, expectancy_r=stats.expectancy_r, profit_factor=stats.profit_factor,
        avg_r=stats.avg_r, max_drawdown_r=drawdown if drawdown is not None else Decimal(0),
        avg_mfe_r=stats.avg_mfe_r, avg_mae_r=stats.avg_mae_r, backtest_version=stats.backtest_version,
        label_version=LABEL_VERSION, ambiguity_policy=AMBIGUITY_POLICY,
        statistics=document(stats.as_document()), data_as_of=data_as_of,
    ).returning(research_backtests.c.id)).scalar_one()
    return int(row)


def last_detection_end_ts(
    conn: Connection, *, ticker: str, timeframe: Timeframe, engine_version: str
) -> datetime | None:
    """The newest candle this engine version has already scanned for a ticker and timeframe.

    Spec 26: a live scan resumes from here instead of recomputing the whole history every cycle.
    """
    value: datetime | None = conn.execute(
        select(func.max(pattern_detections.c.end_ts)).where(and_(
            pattern_detections.c.ticker == ticker,
            pattern_detections.c.timeframe == timeframe.value,
            pattern_detections.c.engine_version == engine_version,
        ))
    ).scalar_one()
    return value


def count_detections(conn: Connection, run_id: UUID) -> int:
    return int(conn.execute(
        select(func.count()).select_from(pattern_detections).where(pattern_detections.c.run_id == run_id)
    ).scalar_one())


def count_candidates(conn: Connection, run_id: UUID) -> int:
    return int(conn.execute(
        select(func.count()).select_from(setup_candidates).where(setup_candidates.c.run_id == run_id)
    ).scalar_one())


def get_run(conn: Connection, run_id: UUID) -> dict[str, Any] | None:
    row = conn.execute(select(research_runs).where(research_runs.c.run_id == run_id)).mappings().first()
    return None if row is None else dict(row)


def latest_runs(
    conn: Connection, *, kind: ResearchRunKind | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    query = select(research_runs).order_by(research_runs.c.started_at.desc(), research_runs.c.run_id).limit(limit)
    if kind is not None:
        query = query.where(research_runs.c.kind == kind.value)
    return [dict(row) for row in conn.execute(query).mappings()]


def candidate_client_signal_ids(conn: Connection, ids: Sequence[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = conn.execute(
        select(setup_candidates.c.id, setup_candidates.c.client_signal_id)
        .where(setup_candidates.c.id.in_(list(ids)))
    )
    return {int(row.id): str(row.client_signal_id) for row in rows}


def record_supersession(
    conn: Connection,
    *,
    fact_type: str,
    superseded_fact_id: int,
    replacement_fact_id: int | None,
    reason: str,
    source_run_id: UUID,
    revision_id: UUID | None,
    superseded_at: datetime,
    input_content_hash: str,
) -> bool:
    """Record that a later revision superseded or retracted a fact. Returns False when already recorded.

    Idempotent on (fact_type, superseded_fact_id, input_content_hash): re-running a scan at the same revision
    must not stack supersession rows for the same correction.
    """
    statement = (
        pg_insert(research_supersessions)
        .values(
            fact_type=fact_type, superseded_fact_id=superseded_fact_id,
            replacement_fact_id=replacement_fact_id, reason=reason, source_run_id=source_run_id,
            revision_id=revision_id, superseded_at=superseded_at, input_content_hash=input_content_hash,
        )
        .on_conflict_do_nothing(index_elements=["fact_type", "superseded_fact_id", "input_content_hash"])
        .returning(research_supersessions.c.id)
    )
    return conn.execute(statement).scalar_one_or_none() is not None


def active_detections(
    conn: Connection,
    *,
    ticker: str,
    timeframe: Timeframe,
    end_ts: datetime,
    engine_version: str,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    """Detections for one bucket that no revision has superseded or retracted.

    With `as_of`, only supersessions recorded at or before that instant count, so the view answers what was
    active then rather than only what is active now.
    """
    gone = select(research_supersessions.c.superseded_fact_id).where(
        research_supersessions.c.fact_type == FACT_PATTERN_DETECTION
    )
    if as_of is not None:
        gone = gone.where(research_supersessions.c.superseded_at <= as_of)
    rows = conn.execute(
        select(
            pattern_detections.c.id, pattern_detections.c.pattern, pattern_detections.c.evidence_hash,
            pattern_detections.c.input_content_hash,
        ).where(and_(
            pattern_detections.c.ticker == ticker,
            pattern_detections.c.timeframe == timeframe.value,
            pattern_detections.c.end_ts == end_ts,
            pattern_detections.c.engine_version == engine_version,
            pattern_detections.c.id.not_in(gone),
        )).order_by(pattern_detections.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]
