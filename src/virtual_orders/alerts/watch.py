"""Watchlist ingestion and alert-rule evaluation for the worker's own `watchlist` job (D20, D26, D27). No fallback."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from core.domain.calendar import ONE_MINUTE, Session
from core.domain.models import Bar
from virtual_orders.alerts.outbox import AlertKind, enqueue_alert, last_alert_ts
from virtual_orders.alerts.rules import pressure_alert, price_crossings
from virtual_orders.alerts.watchlist import AlertRule, RuleKind, list_rules, watchlist_tickers
from virtual_orders.analytics.pressure import DISCLAIMER, METHOD
from virtual_orders.evaluator.clock import require_aware
from virtual_orders.evaluator.outcomes import ERROR_PREFIX
from virtual_orders.ledger.runs import RunInfo, RunKind, RunStatus, finish_run, start_run
from virtual_orders.marketdata.asof import acquire_data_as_of, floor_minute, read_bars_as_of
from virtual_orders.marketdata.calendars import calendar_for_window
from virtual_orders.marketdata.gateway import MarketDataGateway, UnknownDataSource
from virtual_orders.marketdata.ingest import ingest_bars
from virtual_orders.marketdata.sources import BarSource, SourceDataError, SourceUnavailable

WATCH_GRACE = timedelta(minutes=5)

_LATEST_BAR = text(
    "SELECT max(ts) FROM bars_1m WHERE ticker = :ticker AND source = :source AND ts >= :start AND ts < :end"
)


@dataclass(frozen=True)
class WatchlistReport:
    run_id: UUID | None
    tickers: tuple[str, ...] = ()
    ingest_failures: dict[str, str] = field(default_factory=dict)
    enqueued: tuple[str, ...] = ()
    rule_errors: dict[str, str] = field(default_factory=dict)


def watch_session(now: datetime) -> Session | None:
    return next((s for s in calendar_for_window(now, now).sessions
                 if s.open_utc <= now <= s.close_utc + WATCH_GRACE), None)


def first_minute_at_or_after(ts: datetime) -> datetime:
    floored = floor_minute(ts)
    return floored if floored == ts.astimezone(UTC) else floored + ONE_MINUTE


def _bar_document(item: Bar) -> dict[str, Any]:
    return {"ts": item.ts, "open": item.open, "high": item.high, "low": item.low, "close": item.close,
            "volume": item.volume, "batch_id": item.batch_id}


def _evaluate_rule(conn: Connection, rule: AlertRule, bars: list[Bar], run: RunInfo, price_source: str) -> list[str]:
    usable_from = first_minute_at_or_after(rule.created_at)  # D1 spirit: never a bar that straddles the creation
    cooldown = timedelta(minutes=rule.cooldown_minutes)
    base = {"rule_id": rule.id, "ticker": rule.ticker, "price_source": price_source, "data_as_of": run.data_as_of,
            "run_id": run.run_id}
    keys: list[str] = []
    if rule.kind is RuleKind.PRICE_CROSS:
        if rule.level is None or rule.direction is None:
            raise ValueError(f"price-cross rule {rule.id} without level or direction")
        before = [item for item in bars if item.ts < usable_from]
        window = before[-1:] + [item for item in bars if item.ts >= usable_from]  # the prior bar only feeds the close
        last = last_alert_ts(conn, AlertKind.PRICE_CROSS, str(rule.id))
        for item in price_crossings(window, level=rule.level, direction=rule.direction.value, cooldown=cooldown,
                                    last_alert_ts=last):
            alert_key = f"PRICE_CROSS:{rule.id}:{item.ts.isoformat()}"
            document = {**base, "level": rule.level, "direction": rule.direction, "bar": _bar_document(item)}
            if enqueue_alert(conn, alert_key=alert_key, kind=AlertKind.PRICE_CROSS, document=document,
                             subject=str(rule.id), subject_ts=item.ts):
                keys.append(alert_key)
        return keys
    if rule.cmf_threshold is None or rule.window_bars is None:
        raise ValueError(f"pressure rule {rule.id} without threshold or window")
    found = pressure_alert([item for item in bars if item.ts >= usable_from], cmf_threshold=rule.cmf_threshold,
                           window_bars=rule.window_bars, cooldown=cooldown,
                           last_alert_ts=last_alert_ts(conn, AlertKind.PRESSURE, str(rule.id)))
    if found is None:
        return keys
    estimate, side = found
    alert_key = f"PRESSURE:{rule.id}:{estimate.last_bar_ts.isoformat()}"
    document = {**base, "estimate": True, "method": METHOD, "disclaimer": DISCLAIMER, "side": side,
                "cmf_threshold": rule.cmf_threshold, "window_bars": rule.window_bars, "values": asdict(estimate)}
    if enqueue_alert(conn, alert_key=alert_key, kind=AlertKind.PRESSURE, document=document, subject=str(rule.id),
                     subject_ts=estimate.last_bar_ts):
        keys.append(alert_key)
    return keys


def _ingest(engine: Engine, gateway: MarketDataGateway, price_source: str, tickers: tuple[str, ...],
            session: Session, end: datetime) -> dict[str, str]:
    failures: dict[str, str] = {}
    try:
        source: BarSource | None = gateway.bar_source(price_source)
    except UnknownDataSource:
        source = None
    for ticker in tickers:
        feed = f"{price_source}:{ticker}"
        if source is None:
            failures[feed] = "UNKNOWN_DATA_SOURCE"
            continue
        with engine.connect() as conn:
            latest: datetime | None = conn.execute(_LATEST_BAR, {
                "ticker": ticker, "source": price_source, "start": session.open_utc, "end": end,
            }).scalar_one()
        begin = session.open_utc if latest is None else latest + ONE_MINUTE
        try:
            ingest_bars(engine, source, ticker, begin, end)
        except (SourceUnavailable, SourceDataError) as exc:
            failures[feed] = type(exc).__name__
        except Exception as exc:  # noqa: BLE001 - D51 (T13): one ticker's unexpected error never aborts the others
            failures[feed] = f"{ERROR_PREFIX}{type(exc).__name__}"
    return failures


def run_watchlist_cycle(
    engine: Engine,
    gateway: MarketDataGateway,
    *,
    price_source: str,
    code_version: str,
    market_now: datetime,
    alerts_enabled: bool,
) -> WatchlistReport:
    now = require_aware(market_now, "market_now")
    session = watch_session(now)
    if session is None:
        return WatchlistReport(None)
    with engine.connect() as conn:
        tickers = tuple(watchlist_tickers(conn))
        rules = list_rules(conn) if alerts_enabled else []
    if not tickers:
        return WatchlistReport(None)
    end = min(floor_minute(now), session.close_utc)
    failures = _ingest(engine, gateway, price_source, tickers, session, end)
    data_as_of = acquire_data_as_of(engine)
    run: RunInfo | None = None
    try:
        with engine.begin() as conn:
            run = start_run(conn, RunKind.WATCHLIST, data_as_of, code_version,
                            detail={"tickers": list(tickers), "price_source": price_source, "market_now": now})
        enqueued: list[str] = []
        rule_errors: dict[str, str] = {}
        for rule in rules:
            if f"{price_source}:{rule.ticker}" in failures:
                continue
            try:
                with engine.begin() as conn:
                    bars = read_bars_as_of(conn, rule.ticker, price_source, session.open_utc, end, run.data_as_of)
                    keys = _evaluate_rule(conn, rule, bars, run, price_source)
                enqueued.extend(keys)
            except Exception as exc:  # noqa: BLE001 - one rule never stops the others (reported in the run detail)
                rule_errors[str(rule.id)] = f"{ERROR_PREFIX}{type(exc).__name__}"
        with engine.begin() as conn:
            finish_run(conn, run.run_id, RunStatus.COMPLETED, {
                "tickers": list(tickers), "price_source": price_source, "market_now": now,
                "ingest_failures": failures, "alerts_enabled": alerts_enabled, "enqueued": len(enqueued),
                "rule_errors": rule_errors,
            })
        return WatchlistReport(run.run_id, tickers, failures, tuple(enqueued), rule_errors)
    except Exception as exc:
        if run is not None:
            with engine.begin() as conn:
                finish_run(conn, run.run_id, RunStatus.FAILED, {"error": repr(exc), "market_now": now})
        raise
