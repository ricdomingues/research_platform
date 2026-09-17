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

from sqlalchemy import Engine

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
from virtual_orders.research.indicators import IndicatorSeries, compute_series
from virtual_orders.research.levels import DEFAULT_POLICY, LevelPolicy, propose_levels
from virtual_orders.research.market_structure import (
    PRIOR_TREND_LOOKBACK,
    MarketStructure,
    structure_at,
    swing_points,
)
from virtual_orders.research.models import Candle, PatternDetection, Timeframe
from virtual_orders.research.repository import (
    ResearchRunKind,
    ResearchRunStatus,
    count_candidates,
    count_detections,
    finish_run,
    last_detection_end_ts,
    record_candidate,
    record_detection,
    start_run,
)
from virtual_orders.research.scoring import DEFAULT_WEIGHTS, ScoreWeights, score_setup
from virtual_orders.research.setups import SetupCandidate, build_candidate
from virtual_orders.research.timeframes import resample

RESEARCH_PRESSURE_WINDOW_BARS = 30  # the platform's own pressure window (D44), reused rather than redefined
RESEARCH_PRESSURE_CMF_THRESHOLD = Decimal("0.05")  # the D37/D66 convention, reused for the same reason
DEFAULT_TIMEFRAMES = (Timeframe.M15, Timeframe.H1, Timeframe.D1)
DEFAULT_LOOKBACK_SESSIONS = 30
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

    def __post_init__(self) -> None:
        if not self.timeframes:
            raise ValueError("a scan needs at least one timeframe")
        if self.lookback_sessions < 1:
            raise ValueError("lookback_sessions must be >= 1")
        if self.pressure_window_bars < 2:
            raise ValueError("pressure_window_bars must be >= 2")

    def snapshot(self) -> dict[str, Any]:
        return {
            "timeframes": [item.value for item in self.timeframes],
            "lookback_sessions": self.lookback_sessions,
            "thresholds": self.thresholds.snapshot(),
            "weights": self.weights.snapshot(),
            "level_policy": self.level_policy.snapshot(),
            "prior_lookback": self.prior_lookback,
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
) -> tuple[int, int, int]:
    """Scan one ticker and timeframe in its own transaction. Returns (candles, detections, candidates)."""
    with engine.begin() as conn:
        bars = read_bars_as_of(conn, ticker, price_source, start, end, data_as_of)
        if not bars:
            return 0, 0, 0
        candles = resample(bars, calendar=calendar, timeframe=timeframe, start=start, end=end,
                           completed_through=completed_through)
        if not candles:
            return 0, 0, 0
        resume = last_detection_end_ts(conn, ticker=ticker, timeframe=timeframe, engine_version=ENGINE_VERSION)
        series = compute_series(candles)
        pivots = swing_points(candles)
        detections = candidates = 0
        for index, candle in enumerate(candles):
            if resume is not None and candle.end_ts <= resume:
                continue  # already scanned by this engine version: a rerun writes nothing
            found = detect_at(
                candles[: index + 1], index,
                prior=prior_context(candles, index, lookback=config.prior_lookback),
                thresholds=config.thresholds,
            )
            if config.patterns is not None:
                found = [item for item in found if item.pattern in config.patterns]
            if not found:
                continue
            structure = structure_at(candles[: index + 1], index, series, swings=pivots)
            estimate, side = _pressure_at(bars, candle, config.pressure_window_bars)
            bounds = _session_bounds(calendar, candle)
            atr_value = series.atr_at(index)
            for detection in found:
                detection_id, inserted = record_detection(
                    conn, run_id=run_id, ticker=ticker, price_source=price_source, detection=detection,
                    data_as_of=data_as_of,
                )
                detections += int(inserted)
                candidate = _candidate_for(
                    ticker=ticker, candles=candles, index=index, detection=detection, series=series,
                    structure=structure, estimate=estimate, side=side, bounds=bounds, atr_value=atr_value,
                    data_as_of=data_as_of, config=config,
                )
                if candidate is None:
                    continue
                _, added = record_candidate(conn, run_id=run_id, detection_id=detection_id, candidate=candidate)
                candidates += int(added)
        return len(candles), detections, candidates


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
    detections = candidates = 0
    try:
        for ticker in symbols:
            for timeframe in config.timeframes:
                key = f"{ticker}:{timeframe.value}"
                try:
                    seen, found, made = _scan_one(
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
        with engine.begin() as conn:
            finish_run(conn, run.run_id, ResearchRunStatus.COMPLETED, {
                "tickers": list(symbols), "price_source": price_source, "market_now": now,
                "candles_scanned": scanned, "failures": failures,
                "detections": count_detections(conn, run.run_id),
                "candidates": count_candidates(conn, run.run_id),
            })
        return ScanReport(run.run_id, data_as_of, symbols, detections, candidates, scanned, failures)
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
