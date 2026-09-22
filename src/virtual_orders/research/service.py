"""The `RESEARCH_SCAN` job: stored bars in, research observations out (Plan 5, D91).

Deliberately **not** part of the live evaluator cycle. Order evaluation is the platform's critical path and the
worker keeps it isolated (D20, D26); research scanning is a second, independent job that reads what ingestion
already stored and writes only to its own tables. It cannot delay a fill, and a failure here cannot fail an
order.

Four properties, each load-bearing:

* **No provider call, ever.** The scan reads `bars_1m` through the existing `data_as_of` watermark. There is no
  gateway, no source and no adapter import in this module, and the import-boundary test pins that.
* **One watermark per run.** Every ticker and every timeframe of a scan reads the same `data_as_of`, so the run
  is a single coherent snapshot of what was known, and re-running it at that watermark reproduces it exactly.
* **Per-ticker isolation.** Each (ticker, timeframe) is its own transaction. One bad symbol is recorded as a
  failure code in the run detail and the scan carries on; nothing it already wrote is rolled back by a later
  problem.
* **Rerun-safe.** Persistence is idempotent at the schema level, and detection resumes from the newest candle
  this engine version already stored. A candle that produced no detection is simply re-examined next time,
  which costs a read and writes nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine

from core.domain.calendar import Session, SessionCalendar
from core.domain.models import Bar
from virtual_orders.alerts.watchlist import watchlist_tickers
from virtual_orders.analytics.pressure import (
    PressureEstimate,
    PressureSide,
    estimate_pressure,
    strong_pressure,
)
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.outcomes import ERROR_PREFIX
from virtual_orders.marketdata.asof import acquire_data_as_of, floor_minute, read_bars_as_of
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.research.backtest import direction_of, prior_context
from virtual_orders.research.candlesticks import DEFAULT_THRESHOLDS, ENGINE_VERSION, PatternThresholds, detect_at
from virtual_orders.research.features import build_snapshot
from virtual_orders.research.identity import candle_input_hash
from virtual_orders.research.indicators import IndicatorSeries, compute_series
from virtual_orders.research.levels import DEFAULT_POLICY, LevelPolicy, propose_levels
from virtual_orders.research.market_structure import (
    PRIOR_TREND_LOOKBACK,
    MarketStructure,
    structure_at,
    swing_points,
)
from virtual_orders.research.models import Candle, PatternDetection, Timeframe
from virtual_orders.research.reconcile import Reconciliation, reconcile
from virtual_orders.research.repository import (
    FACT_PATTERN_DETECTION,
    NO_LONGER_DETECTED,
    SUPERSEDED_BY_REVISION,
    ResearchRunKind,
    ResearchRunStatus,
    active_detections,
    count_candidates,
    count_detections,
    finish_run,
    last_detection_end_ts,
    record_candidate,
    record_detection,
    record_supersession,
    start_run,
)
from virtual_orders.research.scoring import DEFAULT_WEIGHTS, ScoreWeights, score_setup
from virtual_orders.research.setups import SetupCandidate, build_candidate
from virtual_orders.research.timeframes import resample, sessions_in_window, settled

RESEARCH_PRESSURE_WINDOW_BARS = 30  # the platform's own pressure window (D44), reused rather than redefined
RESEARCH_PRESSURE_CMF_THRESHOLD = Decimal("0.05")  # the D37/D66 convention, reused for the same reason
DEFAULT_TIMEFRAMES = (Timeframe.M15, Timeframe.H1, Timeframe.D1)
DEFAULT_LOOKBACK_SESSIONS = 30
# How many recent sessions are re-examined even where a previous scan already read them. `settled()` keeps
# the engine from reading a bucket before its bars have landed, but a bucket the vendor corrects *after*
# that is past the resume watermark and would never be looked at again. Two sessions covers the same-day
# and next-day corrections a feed actually sends; re-reading an unchanged candle writes nothing, so the
# window costs CPU and no rows.
DEFAULT_REVISIT_SESSIONS = 2
# Ingestion runs every two minutes, so a bucket's last bars can land up to about that late. Three minutes
# gives a closed bucket one full ingestion cycle plus slack before a scan is allowed to call it final.
DEFAULT_SETTLE_MINUTES = 3
CALENDAR_PAD_DAYS = 10
NO_WATCHLIST = "NO_WATCHLIST"
NO_SESSIONS = "NO_SESSIONS"


@dataclass(frozen=True)
class ScanConfig:
    """Everything that changes what a scan produces. Hashed into the run, so a result names the rules behind it."""

    timeframes: tuple[Timeframe, ...] = DEFAULT_TIMEFRAMES
    lookback_sessions: int = DEFAULT_LOOKBACK_SESSIONS
    thresholds: PatternThresholds = DEFAULT_THRESHOLDS
    weights: ScoreWeights = DEFAULT_WEIGHTS
    level_policy: LevelPolicy = DEFAULT_POLICY
    prior_lookback: int = PRIOR_TREND_LOOKBACK
    patterns: tuple[str, ...] | None = None
    pressure_window_bars: int = RESEARCH_PRESSURE_WINDOW_BARS
    # How long a closed bucket is given for its last bars to arrive before it is read as final. A bucket whose
    # minutes have all *elapsed* is not a bucket whose bars have all *landed*: ingestion runs on its own
    # cadence and a scan that reads across that gap sees a candle that is still changing shape.
    settle_minutes: int = DEFAULT_SETTLE_MINUTES
    # Sessions re-examined regardless of the resume watermark, so a corrected bar cannot freeze a stale
    # reading. 0 disables the revisit and restores pure resume-only scanning.
    revisit_sessions: int = DEFAULT_REVISIT_SESSIONS

    def __post_init__(self) -> None:
        if not self.timeframes:
            raise ValueError("a scan needs at least one timeframe")
        if self.lookback_sessions < 1:
            raise ValueError("lookback_sessions must be >= 1")
        if self.pressure_window_bars < 2:
            raise ValueError("pressure_window_bars must be >= 2")
        if self.settle_minutes < 0:
            raise ValueError("settle_minutes must be >= 0")
        if self.revisit_sessions < 0:
            raise ValueError("revisit_sessions must be >= 0")

    def snapshot(self) -> dict[str, Any]:
        return {
            "timeframes": [item.value for item in self.timeframes],
            "lookback_sessions": self.lookback_sessions,
            "thresholds": self.thresholds.snapshot(),
            "weights": self.weights.snapshot(),
            "level_policy": self.level_policy.snapshot(),
            "prior_lookback": self.prior_lookback,
            "settle_minutes": self.settle_minutes,
            "revisit_sessions": self.revisit_sessions,
            "patterns": None if self.patterns is None else list(self.patterns),
            "pressure_window_bars": self.pressure_window_bars,
            "engine_version": ENGINE_VERSION,
        }


DEFAULT_CONFIG = ScanConfig()


@dataclass(frozen=True)
class ScanReport:
    run_id: UUID | None
    data_as_of: datetime | None = None
    tickers: tuple[str, ...] = ()
    detections: int = 0
    candidates: int = 0
    # Stored facts this scan retired: readings a revision replaced, plus readings it retracted outright (D96).
    supersessions: int = 0
    scanned: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    skipped: str | None = None

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def _window(calendar: SessionCalendar, now: datetime, sessions: int) -> tuple[datetime, datetime] | None:
    """The opening minute of the `sessions`-th most recent session that has already opened, through `now`."""
    started = [item for item in calendar.sessions if item.open_utc <= now]
    if not started:
        return None
    return started[-sessions:][0].open_utc, now


def _session_bounds(calendar: SessionCalendar, candle: Candle) -> tuple[datetime, datetime] | None:
    session: Session | None = next(
        (item for item in calendar.sessions if item.day == candle.session_day), None
    )
    return None if session is None else (session.open_utc, session.close_utc)


def _pressure_at(
    bars: Sequence[Bar], candle: Candle, window_bars: int
) -> tuple[PressureEstimate | None, PressureSide | None]:
    """The platform's OHLCV pressure estimate over the stored minutes up to this candle's last minute.

    Cut at the candle, never after it: an estimate that reached one minute past the candle would be a leak into
    a snapshot that claims to describe only what was known at it.
    """
    usable = [item for item in bars if item.ts <= candle.end_ts][-window_bars:]
    if len(usable) < window_bars:
        return None, None
    estimate = estimate_pressure(usable)
    if estimate is None:
        return None, None
    return estimate, strong_pressure(estimate, RESEARCH_PRESSURE_CMF_THRESHOLD)


def _candidate_for(
    *,
    ticker: str,
    candles: Sequence[Candle],
    index: int,
    detection: PatternDetection,
    series: IndicatorSeries,
    structure: MarketStructure,
    estimate: PressureEstimate | None,
    side: PressureSide | None,
    bounds: tuple[datetime, datetime] | None,
    atr_value: Decimal | None,
    data_as_of: datetime,
    config: ScanConfig,
) -> SetupCandidate | None:
    """Assemble the candidate for one detection, or None when the pattern carries no tradable direction."""
    direction = direction_of(detection)
    if direction is None:
        return None
    snapshot = build_snapshot(
        list(candles[: index + 1]), index, ticker=ticker, data_as_of=data_as_of, series=series,
        structure=structure, detection=detection, pressure=estimate, pressure_side=side,
        session_open=None if bounds is None else bounds[0],
        session_close=None if bounds is None else bounds[1],
    )
    score = score_setup(detection, snapshot, direction, weights=config.weights)
    levels = None
    if atr_value is not None and atr_value > Decimal(0):
        window = list(candles[max(0, index - detection.candles + 1) : index + 1])
        levels = propose_levels(window, direction=detection.direction, atr=atr_value, structure=structure,
                                policy=config.level_policy)
    return build_candidate(ticker=ticker, detection=detection, snapshot=snapshot, score=score,
                           data_as_of=data_as_of, levels=levels)


def _revisit_floor(
    calendar: SessionCalendar, *, start: datetime, end: datetime, sessions_back: int
) -> datetime | None:
    """The instant from which candles are re-examined even if a previous scan already read them.

    Returns the open of the `sessions_back`-th most recent session in the window, or None when the revisit is
    disabled or the window holds no session.
    """
    if sessions_back < 1:
        return None
    sessions = sessions_in_window(calendar, start, end)
    if not sessions:
        return None
    return sessions[-sessions_back:][0].open_utc


def _apply_reconciliation(
    conn: Connection,
    *,
    outcome: Reconciliation,
    run_id: UUID,
    ticker: str,
    timeframe: Timeframe,
    end_ts: datetime,
    input_hash: str,
    superseded_at: datetime,
) -> None:
    """Write the supersession facts, resolving replacement ids after the new readings have been stored.

    A replacement id can only be recorded once its row exists, and the replacement rows are written by the
    normal detection path earlier in this same transaction, so the ids are read back here rather than guessed.
    """
    if not outcome.superseded and not outcome.retracted:
        return
    by_hash = {
        row["evidence_hash"]: row["id"]
        for row in active_detections(conn, ticker=ticker, timeframe=timeframe, end_ts=end_ts,
                                     engine_version=ENGINE_VERSION)
    }
    for fact_id, replacement_hash in outcome.superseded:
        replacement_id = by_hash.get(replacement_hash)
        if replacement_id is None:
            continue  # no row to point at, so no link to record; the next scan sees the same correction
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=fact_id,
            replacement_fact_id=int(replacement_id), reason=SUPERSEDED_BY_REVISION, source_run_id=run_id,
            revision_id=None, superseded_at=superseded_at, input_content_hash=input_hash,
        )
    for fact_id in outcome.retracted:
        record_supersession(
            conn, fact_type=FACT_PATTERN_DETECTION, superseded_fact_id=fact_id, replacement_fact_id=None,
            reason=NO_LONGER_DETECTED, source_run_id=run_id, revision_id=None,
            superseded_at=superseded_at, input_content_hash=input_hash,
        )


def _scan_one(
    engine: Engine,
    *,
    run_id: UUID,
    ticker: str,
    timeframe: Timeframe,
    price_source: str,
    data_as_of: datetime,
    calendar: SessionCalendar,
    start: datetime,
    end: datetime,
    completed_through: datetime,
    config: ScanConfig,
) -> tuple[int, int, int, int]:
    """Scan one ticker and timeframe in its own transaction.

    Returns (candles, detections, candidates, supersessions).
    """
    with engine.begin() as conn:
        bars = read_bars_as_of(conn, ticker, price_source, start, end, data_as_of)
        if not bars:
            return 0, 0, 0, 0
        candles = resample(bars, calendar=calendar, timeframe=timeframe, start=start, end=end,
                           completed_through=completed_through)
        if not candles:
            return 0, 0, 0, 0
        resume = last_detection_end_ts(conn, ticker=ticker, timeframe=timeframe, engine_version=ENGINE_VERSION)
        revisit_from = _revisit_floor(calendar, start=start, end=end, sessions_back=config.revisit_sessions)
        series = compute_series(candles)
        pivots = swing_points(candles)
        detections = candidates = supersessions = 0
        settle = timedelta(minutes=config.settle_minutes)
        for index, candle in enumerate(candles):
            already_read = resume is not None and candle.end_ts <= resume
            if already_read and (revisit_from is None or candle.end_ts < revisit_from):
                continue  # already scanned by this engine version, and too old to have been corrected since
            if not settled(candle, completed_through=completed_through, settle=settle):
                # Every later candle of this series is newer, so none of them is settled either. Leaving the
                # resume watermark where it is means this bucket is read again next scan, with its real shape.
                break
            input_hash = candle_input_hash(bars, candle)
            found = detect_at(
                candles[: index + 1], index,
                prior=prior_context(candles, index, lookback=config.prior_lookback),
                thresholds=config.thresholds,
            )
            if config.patterns is not None:
                found = [item for item in found if item.pattern in config.patterns]
            if found:
                structure = structure_at(candles[: index + 1], index, series, swings=pivots)
                estimate, side = _pressure_at(bars, candle, config.pressure_window_bars)
                bounds = _session_bounds(calendar, candle)
                atr_value = series.atr_at(index)
                for detection in found:
                    detection_id, inserted = record_detection(
                        conn, run_id=run_id, ticker=ticker, price_source=price_source, detection=detection,
                        data_as_of=data_as_of, input_content_hash=input_hash,
                    )
                    detections += int(inserted)
                    candidate = _candidate_for(
                        ticker=ticker, candles=candles, index=index, detection=detection, series=series,
                        structure=structure, estimate=estimate, side=side, bounds=bounds, atr_value=atr_value,
                        data_as_of=data_as_of, config=config,
                    )
                    if candidate is None:
                        continue
                    _, added = record_candidate(conn, run_id=run_id, detection_id=detection_id,
                                                candidate=candidate, input_content_hash=input_hash)
                    candidates += int(added)
            if not already_read:
                continue  # a bucket read for the first time has no earlier reading of itself to reconcile
            # A revisited bucket. Only a genuine change in the DATA is a reason to reconcile: an identical
            # re-ingestion under a new batch id is a non-event (D96). The new readings are already stored, so
            # the active view here holds both the stale rows and the ones that replace them.
            stored_rows = active_detections(
                conn, ticker=ticker, timeframe=timeframe, end_ts=candle.end_ts,
                engine_version=ENGINE_VERSION,
            )
            unchanged = bool(stored_rows) and all(
                row["input_content_hash"] == input_hash for row in stored_rows
            )
            if unchanged:
                continue
            outcome = reconcile(found, stored_rows)
            _apply_reconciliation(
                conn, outcome=outcome, run_id=run_id, ticker=ticker, timeframe=timeframe,
                end_ts=candle.end_ts, input_hash=input_hash, superseded_at=data_as_of,
            )
            supersessions += len(outcome.superseded) + len(outcome.retracted)
        return len(candles), detections, candidates, supersessions


def run_research_scan(
    engine: Engine,
    *,
    code_version: str,
    price_source: str,
    market_now: datetime,
    config: ScanConfig = DEFAULT_CONFIG,
    tickers: Sequence[str] | None = None,
) -> ScanReport:
    """Scan the watchlist over stored bars only. Never calls a provider and never touches order evaluation."""
    now = require_aware(market_now, "market_now")
    if tickers is None:
        with engine.connect() as conn:
            symbols = tuple(watchlist_tickers(conn))
    else:
        symbols = tuple(sorted(set(tickers)))
    if not symbols:
        return ScanReport(None, skipped=NO_WATCHLIST)

    calendar = calendar_for_window(now - timedelta(days=config.lookback_sessions * 2 + CALENDAR_PAD_DAYS), now)
    window = _window(calendar, now, config.lookback_sessions)
    if window is None:
        return ScanReport(None, skipped=NO_SESSIONS)
    start, end = window
    completed_through = floor_minute(now)

    data_as_of = acquire_data_as_of(engine)  # one watermark for the whole run
    with engine.begin() as conn:
        run = start_run(
            conn, kind=ResearchRunKind.RESEARCH_SCAN, data_as_of=data_as_of, code_version=code_version,
            engine_version=ENGINE_VERSION, configuration=config.snapshot(),
            detail={"tickers": list(symbols), "price_source": price_source, "market_now": now},
        )
    failures: dict[str, str] = {}
    scanned: dict[str, int] = {}
    detections = candidates = supersessions = 0
    try:
        for ticker in symbols:
            for timeframe in config.timeframes:
                key = f"{ticker}:{timeframe.value}"
                try:
                    seen, found, made, retired = _scan_one(
                        engine, run_id=run.run_id, ticker=ticker, timeframe=timeframe,
                        price_source=price_source, data_as_of=data_as_of, calendar=calendar, start=start,
                        end=end, completed_through=completed_through, config=config,
                    )
                except Exception as exc:  # noqa: BLE001 - one symbol never aborts the scan (spec 20)
                    failures[key] = f"{ERROR_PREFIX}{type(exc).__name__}"
                    continue
                scanned[key] = seen
                detections += found
                candidates += made
                supersessions += retired
        with engine.begin() as conn:
            finish_run(conn, run.run_id, ResearchRunStatus.COMPLETED, {
                "tickers": list(symbols), "price_source": price_source, "market_now": now,
                "candles_scanned": scanned, "failures": failures,
                "detections": count_detections(conn, run.run_id),
                "candidates": count_candidates(conn, run.run_id),
            })
        return ScanReport(run.run_id, data_as_of, symbols, detections=detections, candidates=candidates,
                          supersessions=supersessions, scanned=scanned, failures=failures)
    except Exception as exc:
        with engine.begin() as conn:
            finish_run(conn, run.run_id, ResearchRunStatus.FAILED,
                       {"error": f"{ERROR_PREFIX}{type(exc).__name__}", "market_now": now, "failures": failures})
        raise


def scan_configuration(overrides: Mapping[str, Any] | None = None) -> ScanConfig:
    """Build a scan configuration from plain values, for an operator-supplied request or a CLI."""
    if not overrides:
        return DEFAULT_CONFIG
    values: dict[str, Any] = dict(overrides)
    if "timeframes" in values:
        values["timeframes"] = tuple(Timeframe(item) for item in values["timeframes"])
    if values.get("patterns") is not None:
        values["patterns"] = tuple(values["patterns"])
    return ScanConfig(**values)
