"""Watchlist and alert-rule management (owner 2026-09-13, D26). Alerts themselves leave only through n8n."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from virtual_orders.alerts.watchlist import (
    AlertRule,
    add_ticker,
    create_rule,
    delete_rule,
    is_watched,
    list_watchlist,
    parse_rule_body,
    remove_ticker,
)
from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response, read_json_object
from virtual_orders.api.errors import ApiError
from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable
from virtual_orders.services import Services

router = APIRouter()


def _normalized(ticker: str) -> str:
    """D26: stripped and upper-cased, so the result satisfies the signal rule (core TICKER_INVALID)."""
    normalized = ticker.strip().upper()
    if not normalized or any(character.isspace() for character in normalized):
        raise ApiError(422, "TICKER_INVALID", detail={"ticker": ticker})
    return normalized


def _require_tradable(services: Services, ticker: str) -> None:
    """Same gate as POST /signals (D14): only for a ticker not yet watched; never echoes provider text."""
    try:
        status = services.ticker_check.check_ticker(ticker)
    except (SourceUnavailable, SourceDataError) as exc:
        raise ApiError(503, "TICKER_UNVERIFIABLE", detail={
            "ticker": ticker, "check": services.ticker_check.name, "error": type(exc).__name__,
        }) from exc
    if not status.tradable:
        raise ApiError(422, "TICKER_NOT_TRADABLE", status.reason, {"ticker": ticker})


def _add(services: Services, ticker: str) -> bool:
    with services.engine.connect() as conn:
        if is_watched(conn, ticker):
            return False
    _require_tradable(services, ticker)
    with services.engine.begin() as conn:
        return add_ticker(conn, ticker, added_at=services.clock())


def _create(services: Services, ticker: str, body: dict[str, Any]) -> AlertRule:
    draft = parse_rule_body(body)  # validated before any lookup
    with services.engine.begin() as conn:
        return create_rule(conn, ticker, draft, created_at=services.clock())


@router.get("/watchlist")
def get_watchlist(services: ServicesDep) -> Response:
    with services.engine.connect() as conn:
        return json_response({"watchlist": list_watchlist(conn)})


@router.put("/watchlist/{ticker}")
def put_watchlist_ticker(ticker: str, services: ServicesDep) -> Response:
    normalized = _normalized(ticker)
    created = _add(services, normalized)
    return json_response({"ticker": normalized, "status": "CREATED" if created else "EXISTING"},
                         201 if created else 200)


@router.delete("/watchlist/{ticker}")
def delete_watchlist_ticker(ticker: str, services: ServicesDep) -> Response:
    normalized = _normalized(ticker)
    with services.engine.begin() as conn:
        remove_ticker(conn, normalized)
    return json_response({"ticker": normalized, "removed": True})


@router.post("/watchlist/{ticker}/alerts")
async def post_alert_rule(ticker: str, request: Request, services: ServicesDep) -> Response:
    normalized = _normalized(ticker)
    body = await read_json_object(request)
    rule = await run_in_threadpool(_create, services, normalized, body)
    return json_response({"rule": rule}, 201)


@router.delete("/alerts/{rule_id}")
def delete_alert_rule(rule_id: UUID, services: ServicesDep) -> Response:
    with services.engine.begin() as conn:
        delete_rule(conn, rule_id)
    return json_response({"rule_id": rule_id, "removed": True})
