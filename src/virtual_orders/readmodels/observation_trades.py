"""Trade sections of the daily observation report (Plan 4, D64-D66): virtual trades, MFE/MAE, signal -> fill latency
and a pressure ESTIMATE recomputed from stored bars before each signal. Reads only; the estimate is never stored and
never feeds any evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, text

from core.domain.models import Bar
from virtual_orders.analytics.observation import (
    ASSOCIATION_NOTE,
    MIN_TRADES_FOR_BUCKET_MEAN,
    OBSERVATION_CMF_THRESHOLD,
    STRONG_CMF,
    Alignment,
    LatencyStats,
    PressureBucket,
    Strength,
    TradeStats,
    classify_pressure,
    latency_stats,
    pressure_buckets,
    trade_stats,
    whole_seconds,
)
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD, PressureEstimate, estimate_pressure
from virtual_orders.marketdata.asof import floor_minute
from virtual_orders.readmodels.market import INSUFFICIENT_BARS, NO_BARS, market_day_start
from virtual_orders.readmodels.observation_window import ObservationWindow

OBSERVATION_PRESSURE_WINDOW_BARS = 30  # the /market/pressure default (D44); a labelled convention (D66)
CLOSING_EVENT_TYPES = ("TARGET1_HIT", "TARGET2_HIT", "STOPPED", "TIME_EXIT")

# D66: the last :bars stored minutes strictly before the signal minute, across sessions, each at the version known as
# of the report (the same version rule as read_bars_as_of: latest ingested_at, then batch_id).
_LAST_BARS_BEFORE = text(
    """
    SELECT DISTINCT ON (b.ts) b.ts, b.open, b.high, b.low, b.close, b.volume, b.batch_id
    FROM bars_1m b
    JOIN bar_batches bb ON bb.batch_id = b.batch_id
    WHERE b.ticker = :ticker AND b.source = :source AND b.ts < :cutoff AND bb.ingested_at <= :as_of
    ORDER BY b.ts DESC, bb.ingested_at DESC, b.batch_id DESC
    LIMIT :bars
    """
)

_CREATED = text(
    """
    SELECT o.origin, COUNT(*) AS orders FROM orders o
    WHERE NOT o.replay AND o.created_at >= :start AND o.created_at < :end
      AND EXISTS (SELECT 1 FROM order_events e
                  WHERE e.order_id = o.id AND e.type = 'ORDER_CREATED' AND e.recorded_at <= :as_of)
    GROUP BY o.origin
    """
)

_FILLS = text(
    """
    SELECT o.id AS order_id, o.origin, g.ticker, g.created_at AS signal_created_at, e.bar_ts, e.recorded_at
    FROM order_events e
    JOIN orders o ON o.id = e.order_id
    JOIN signals g ON g.id = o.signal_id
    WHERE NOT o.replay AND e.type = 'FILLED' AND e.bar_ts >= :start AND e.bar_ts < :end AND e.recorded_at <= :as_of
    ORDER BY e.bar_ts, o.origin, o.id
    """
)

_UNFILLED = text(
    """
    SELECT COALESCE(outcome.type, 'NOT_FILLED_YET') AS bucket, COUNT(*) AS orders
    FROM orders o
    LEFT JOIN LATERAL (
        SELECT e.type FROM order_events e
        WHERE e.order_id = o.id AND e.type IN ('EXPIRED', 'INVALIDATED', 'CANCELED') AND e.recorded_at <= :as_of
        ORDER BY e.seq LIMIT 1
    ) outcome ON true
    WHERE NOT o.replay AND o.created_at >= :start AND o.created_at < :end
      AND EXISTS (SELECT 1 FROM order_events c
                  WHERE c.order_id = o.id AND c.type = 'ORDER_CREATED' AND c.recorded_at <= :as_of)
      AND NOT EXISTS (SELECT 1 FROM order_events f
                      WHERE f.order_id = o.id AND f.type = 'FILLED' AND f.recorded_at <= :as_of)
    GROUP BY 1
    """
)

_CLOSED = text(
    """
    SELECT o.id AS order_id, o.origin, o.replay, o.price_source, g.ticker, g.direction, g.strategy,
           g.created_at AS signal_created_at, st.opened_at, st.closed_at, st.r_multiple, st.mfe_r, st.mae_r,
           st.needs_review
    FROM orders o
    JOIN signals g ON g.id = o.signal_id
    JOIN order_state st ON st.order_id = o.id
    WHERE st.status = 'CLOSED' AND st.closed_at >= :start AND st.closed_at < :end
      AND EXISTS (
          SELECT 1 FROM order_events e
          WHERE e.order_id = o.id AND e.bar_ts = st.closed_at AND e.recorded_at <= :as_of
            AND e.type = ANY(CAST(:closing AS text[]))
      )
    ORDER BY st.closed_at, o.origin, o.id
    """
)


@dataclass(frozen=True)
class PressureBefore:
    estimate: PressureEstimate | None
    unavailable_reason: str | None
    window_start: datetime | None  # first bar used; None without an estimate
    window_end: datetime | None  # last bar used (strictly before the signal minute)
    spans_sessions: bool | None  # the bars come from more than one ET date (session)


def pressure_before(
    conn: Connection, *, ticker: str, price_source: str, decided_at: datetime, as_of: datetime
) -> PressureBefore:
    """D66: the last 30 stored bars strictly before the signal's decision minute, across sessions, as of the report.

    Never truncated at the session open, so a 09:45 ET signal reads the previous session's last bars too.
    INSUFFICIENT_BARS only when fewer than 30 bars exist at all as of the report; NO_BARS when there is none."""
    rows = conn.execute(_LAST_BARS_BEFORE, {
        "ticker": ticker, "source": price_source, "cutoff": floor_minute(decided_at), "as_of": as_of,
        "bars": OBSERVATION_PRESSURE_WINDOW_BARS,
    }).all()
    if not rows:
        return PressureBefore(None, NO_BARS, None, None, None)
    if len(rows) < OBSERVATION_PRESSURE_WINDOW_BARS:
        return PressureBefore(None, INSUFFICIENT_BARS, None, None, None)
    bars = [Bar(ts=row.ts, open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume,
                batch_id=row.batch_id) for row in reversed(rows)]
    estimate = estimate_pressure(bars)
    # estimate_pressure only returns None for fewer than 2 bars; `bars` is exactly OBSERVATION_PRESSURE_WINDOW_BARS
    # here (INSUFFICIENT_BARS already returned above otherwise), so this can never fire. An explicit assertion
    # fails loudly if OBSERVATION_PRESSURE_WINDOW_BARS is ever dropped below 2, instead of silently mislabelling.
    assert estimate is not None, "a full pressure window must always yield an estimate"
    first, last = bars[0].ts, bars[-1].ts
    return PressureBefore(estimate, None, first, last, market_day_start(first) != market_day_start(last))


@dataclass(frozen=True)
class TradeRow:
    order_id: UUID
    ticker: str
    direction: str
    origin: str
    strategy: str
    opened_at: datetime | None
    closed_at: datetime
    r_multiple: Decimal
    mfe_r: Decimal | None
    mae_r: Decimal | None
    needs_review: bool
    pressure_alignment: Alignment
    pressure_strength: Strength | None
    pressure_cmf: Decimal | None
    pressure_unavailable_reason: str | None
    pressure_window_start: datetime | None
    pressure_window_end: datetime | None
    pressure_spans_sessions: bool | None


@dataclass(frozen=True)
class TradesSection:
    created: dict[str, int]
    filled: int
    closed: int
    excluded_needs_review: int
    stats: TradeStats
    stats_by_origin: dict[str, TradeStats]
    replay_closed: int
    rows: tuple[TradeRow, ...]


@dataclass(frozen=True)
class FillLatencyRow:
    order_id: UUID
    ticker: str
    origin: str
    signal_created_at: datetime
    fill_bar_ts: datetime
    recorded_at: datetime
    bar_latency_seconds: int
    recorded_latency_seconds: int


@dataclass(frozen=True)
class LatencySection:
    bar: LatencyStats
    recorded: LatencyStats
    bar_by_origin: dict[str, LatencyStats]
    unfilled: dict[str, int]
    rows: tuple[FillLatencyRow, ...]


@dataclass(frozen=True)
class PressureResultSection:
    estimate: bool
    method: str
    disclaimer: str
    association_note: str
    window_bars: int
    cmf_threshold: Decimal
    strong_cmf: Decimal
    min_trades_for_mean: int
    buckets: tuple[PressureBucket, ...]
    unavailable_reasons: dict[str, int]


@dataclass(frozen=True)
class TradeFacts:
    trades: TradesSection
    latency: LatencySection
    pressure: PressureResultSection


def pressure_section(rows: list[TradeRow]) -> PressureResultSection:
    """Buckets and unavailable reasons over closed trades without a review flag (spec 5.4, D66)."""
    included = [row for row in rows if not row.needs_review]
    reasons: list[str] = []  # a typed list first: no Optional member narrowing inside a generator
    for row in included:
        if row.pressure_unavailable_reason is not None:
            reasons.append(row.pressure_unavailable_reason)
    return PressureResultSection(
        estimate=True, method=METHOD, disclaimer=DISCLAIMER, association_note=ASSOCIATION_NOTE,
        window_bars=OBSERVATION_PRESSURE_WINDOW_BARS, cmf_threshold=OBSERVATION_CMF_THRESHOLD, strong_cmf=STRONG_CMF,
        min_trades_for_mean=MIN_TRADES_FOR_BUCKET_MEAN,
        buckets=tuple(pressure_buckets((row.pressure_alignment, row.pressure_strength, row.r_multiple)
                                       for row in included)),
        unavailable_reasons=dict(sorted(Counter(reasons).items())),
    )


def latency_section(fills: list[FillLatencyRow], unfilled: dict[str, int]) -> LatencySection:
    by_origin: dict[str, list[int]] = defaultdict(list)
    for fill in fills:
        by_origin[fill.origin].append(fill.bar_latency_seconds)
    return LatencySection(
        bar=latency_stats([fill.bar_latency_seconds for fill in fills]),
        recorded=latency_stats([fill.recorded_latency_seconds for fill in fills]),
        bar_by_origin={origin: latency_stats(values) for origin, values in sorted(by_origin.items())},
        unfilled=dict(sorted(unfilled.items())), rows=tuple(fills),
    )


def trade_sections(conn: Connection, window: ObservationWindow) -> TradeFacts:
    params = {"start": window.start, "end": window.end, "as_of": window.as_of}
    created = {row.origin: int(row.orders) for row in conn.execute(_CREATED, params)}
    fills = [
        FillLatencyRow(row.order_id, row.ticker, row.origin, row.signal_created_at, row.bar_ts, row.recorded_at,
                       whole_seconds(row.signal_created_at, row.bar_ts),
                       whole_seconds(row.signal_created_at, row.recorded_at))
        for row in conn.execute(_FILLS, params)
    ]
    unfilled = {row.bucket: int(row.orders) for row in conn.execute(_UNFILLED, params)}
    rows: list[TradeRow] = []
    replay_closed = 0
    for row in conn.execute(_CLOSED, {**params, "closing": list(CLOSING_EVENT_TYPES)}).all():
        if row.replay:
            replay_closed += 1  # D64: counted and labelled, never mixed into the trade metrics
            continue
        pressure = pressure_before(conn, ticker=row.ticker, price_source=row.price_source,
                                   decided_at=row.signal_created_at, as_of=window.as_of)
        alignment, strength = classify_pressure(pressure.estimate, row.direction)
        rows.append(TradeRow(
            order_id=row.order_id, ticker=row.ticker, direction=row.direction, origin=row.origin,
            strategy=row.strategy, opened_at=row.opened_at, closed_at=row.closed_at, r_multiple=row.r_multiple,
            mfe_r=row.mfe_r, mae_r=row.mae_r, needs_review=row.needs_review, pressure_alignment=alignment,
            pressure_strength=strength,
            pressure_cmf=None if pressure.estimate is None else pressure.estimate.chaikin_money_flow,
            pressure_unavailable_reason=pressure.unavailable_reason, pressure_window_start=pressure.window_start,
            pressure_window_end=pressure.window_end, pressure_spans_sessions=pressure.spans_sessions,
        ))
    included = [row for row in rows if not row.needs_review]
    trades = TradesSection(
        created=dict(sorted(created.items())), filled=len(fills), closed=len(rows),
        excluded_needs_review=len(rows) - len(included),
        stats=trade_stats([(row.r_multiple, row.mfe_r, row.mae_r) for row in included]),
        stats_by_origin={
            origin: trade_stats([(row.r_multiple, row.mfe_r, row.mae_r) for row in included if row.origin == origin])
            for origin in sorted({row.origin for row in included})
        },
        replay_closed=replay_closed, rows=tuple(rows),
    )
    return TradeFacts(trades, latency_section(fills, unfilled), pressure_section(rows))
