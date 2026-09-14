"""Dashboard market views (D41-D44): stored bars only, read as of the ingestion watermark. Never calls a provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, select, text

from core.domain.calendar import ONE_MINUTE
from core.domain.models import Bar
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD, PressureEstimate, estimate_pressure, strong_pressure
from virtual_orders.analytics.vwap import METHOD as VWAP_METHOD
from virtual_orders.analytics.vwap import VwapPoint, session_vwap
from virtual_orders.ledger.errors import OrderNotFound
from virtual_orders.ledger.events import stored_events
from virtual_orders.marketdata.asof import read_bars_as_of
from virtual_orders.storage.codec import parse_ts
from virtual_orders.storage.tables import order_state, orders, signals

MAX_BARS_WINDOW = timedelta(days=7)
NO_BARS = "NO_BARS"
INSUFFICIENT_BARS = "INSUFFICIENT_BARS"
SIGNAL_LEVELS = ("entry_zone_low", "entry_zone_high", "stop", "target1", "target2", "trigger_price")
_MARKET_TZ = ZoneInfo("America/New_York")

_LAST_BAR = text(
    """
    SELECT max(b.ts) FROM bars_1m b JOIN bar_batches bb ON bb.batch_id = b.batch_id
    WHERE b.ticker = :ticker AND b.source = :source AND bb.ingested_at <= :as_of
    """
)

_CHART_COLUMNS = (
    orders.c.id.label("order_id"), orders.c.origin, orders.c.price_source, orders.c.replay,
    orders.c.replay_of_order_id, orders.c.fill_model_version, orders.c.config_snapshot, orders.c.evaluation_start_ts,
    orders.c.valid_until_ts, signals.c.ticker, signals.c.direction, signals.c.strategy,
    *(signals.c[name] for name in SIGNAL_LEVELS),
    order_state.c.status, order_state.c.avg_entry, order_state.c.stop_current, order_state.c.last_bar_ts,
    order_state.c.final_event_ts,
)


def market_day_start(ts: datetime) -> datetime:
    """00:00 America/New_York of the ET date of `ts`: every regular session of that date starts after it."""
    return datetime.combine(ts.astimezone(_MARKET_TZ).date(), time(0), tzinfo=_MARKET_TZ)


@dataclass(frozen=True)
class CandleSeries:
    ticker: str
    price_source: str
    data_as_of: datetime
    start: datetime
    end: datetime
    vwap_method: str
    bars: tuple[Bar, ...]
    vwap: tuple[VwapPoint, ...]


@dataclass(frozen=True)
class PressureView:
    ticker: str
    price_source: str
    data_as_of: datetime
    window_bars: int
    estimate: bool
    method: str
    disclaimer: str
    available: bool
    reason: str | None
    values: PressureEstimate | None
    cmf_threshold: Decimal | None
    side: str | None


@dataclass(frozen=True)
class OrderChart:
    order_id: UUID
    ticker: str
    direction: str
    strategy: str
    origin: str
    status: str | None
    price_source: str
    replay: bool
    replay_of_order_id: UUID | None
    fill_model_version: str
    config_snapshot: dict[str, Any]
    evaluation_start_ts: datetime
    valid_until_ts: datetime
    levels: dict[str, Decimal | None]
    markers: tuple[dict[str, Any], ...]
    window: dict[str, datetime]
    window_truncated: bool  # M10: the 7-day cap cut the end of the order's window


def candles(
    conn: Connection, *, ticker: str, price_source: str, start: datetime, end: datetime, as_of: datetime
) -> CandleSeries:
    """D43: bars are read from 00:00 ET of the first day so the VWAP is anchored at the session open."""
    anchored = read_bars_as_of(conn, ticker, price_source, market_day_start(start), end, as_of)
    vwap = session_vwap(anchored)
    return CandleSeries(
        ticker, price_source, as_of, start, end, VWAP_METHOD,
        tuple(item for item in anchored if item.ts >= start), tuple(point for point in vwap if point.ts >= start),
    )


def latest_pressure(
    conn: Connection,
    *,
    ticker: str,
    price_source: str,
    window_bars: int,
    cmf_threshold: Decimal | None,
    as_of: datetime,
) -> PressureView:
    """D44: the last `window_bars` bars of the ET date of the latest stored bar; always labelled as an estimate."""

    def view(available: bool, reason: str | None, values: PressureEstimate | None = None,
             side: str | None = None) -> PressureView:
        return PressureView(ticker, price_source, as_of, window_bars, True, METHOD, DISCLAIMER, available, reason,
                            values, cmf_threshold, side)

    last: datetime | None = conn.execute(_LAST_BAR, {"ticker": ticker, "source": price_source,
                                                     "as_of": as_of}).scalar_one()
    if last is None:
        return view(False, NO_BARS)
    bars = read_bars_as_of(conn, ticker, price_source, market_day_start(last), last + ONE_MINUTE, as_of)[-window_bars:]
    estimate = estimate_pressure(bars) if len(bars) == window_bars else None
    if estimate is None:
        return view(False, INSUFFICIENT_BARS)
    side = None if cmf_threshold is None else strong_pressure(estimate, cmf_threshold)
    return view(True, None, estimate, None if side is None else side.value)


def order_chart(conn: Connection, order_id: UUID) -> OrderChart:
    row = conn.execute(
        select(*_CHART_COLUMNS)
        .select_from(orders.join(signals, signals.c.id == orders.c.signal_id)
                     .outerjoin(order_state, order_state.c.order_id == orders.c.id))
        .where(orders.c.id == order_id)
    ).mappings().first()
    if row is None:
        raise OrderNotFound(str(order_id))
    markers: list[dict[str, Any]] = []
    for item in stored_events(conn, order_id):
        event = item.prepared
        ts = event.bar_ts
        if ts is None and event.type == "DATA_GAP":
            raw = event.payload.get("gap_start_ts")
            ts = parse_ts(raw) if isinstance(raw, str) else None
        if ts is not None:
            markers.append({"seq": item.seq, "type": event.type, "event_key": event.event_key, "ts": ts,
                            "price": event.price})
    levels: dict[str, Decimal | None] = {name: row[name] for name in SIGNAL_LEVELS}
    levels.update(avg_entry=row["avg_entry"], stop_current=row["stop_current"])
    start = market_day_start(row["evaluation_start_ts"])
    anchor = row["final_event_ts"] or row["last_bar_ts"] or row["evaluation_start_ts"]
    natural_end, capped_end = anchor + ONE_MINUTE, start + MAX_BARS_WINDOW
    return OrderChart(
        order_id=row["order_id"], ticker=row["ticker"], direction=row["direction"], strategy=row["strategy"],
        origin=row["origin"], status=row["status"], price_source=row["price_source"], replay=row["replay"],
        replay_of_order_id=row["replay_of_order_id"], fill_model_version=row["fill_model_version"],
        config_snapshot=dict(row["config_snapshot"]), evaluation_start_ts=row["evaluation_start_ts"],
        valid_until_ts=row["valid_until_ts"], levels=levels, markers=tuple(markers),
        window={"from": start, "to": min(natural_end, capped_end)}, window_truncated=natural_end > capped_end,
    )
