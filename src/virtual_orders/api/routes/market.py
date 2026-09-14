"""Market views for the dashboard (D41-D44): stored bars only, as of the ingestion watermark; never a provider call."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from virtual_orders.alerts.watchlist import MAX_WINDOW_BARS, MIN_WINDOW_BARS
from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.api.errors import ApiError
from virtual_orders.api.tickers import normalize_ticker
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.market import MAX_BARS_WINDOW, candles, latest_pressure, order_chart

router = APIRouter()
DEFAULT_PRESSURE_WINDOW_BARS = 30


def _invalid(errors: list[str]) -> ApiError:
    return ApiError(422, "MARKET_REQUEST_INVALID", detail={"errors": errors})


@router.get("/market/bars")
def get_market_bars(
    services: ServicesDep,
    ticker: str,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
    source: str | None = None,
) -> Response:
    normalized = normalize_ticker(ticker)
    errors = [f"NAIVE_DATETIME:{name}" for name, value in (("from", start), ("to", end)) if value.tzinfo is None]
    if not errors and end <= start:
        errors.append("EMPTY_WINDOW")
    if not errors and end - start > MAX_BARS_WINDOW:
        errors.append("WINDOW_TOO_LARGE")
    if errors:
        raise _invalid(errors)
    as_of = acquire_data_as_of(services.engine)
    with services.engine.connect() as conn:
        series = candles(conn, ticker=normalized, price_source=source or services.price_source, start=start, end=end,
                         as_of=as_of)
    return json_response(series)


@router.get("/market/pressure")
def get_market_pressure(
    services: ServicesDep,
    ticker: str,
    window_bars: int = DEFAULT_PRESSURE_WINDOW_BARS,
    cmf_threshold: Decimal | None = None,
    source: str | None = None,
) -> Response:
    normalized = normalize_ticker(ticker)
    errors: list[str] = []
    if not MIN_WINDOW_BARS <= window_bars <= MAX_WINDOW_BARS:
        errors.append("OUT_OF_RANGE:window_bars")
    if cmf_threshold is not None and not (cmf_threshold.is_finite() and 0 < cmf_threshold < 1):
        errors.append("OUT_OF_RANGE:cmf_threshold")
    if errors:
        raise _invalid(errors)
    as_of = acquire_data_as_of(services.engine)
    with services.engine.connect() as conn:
        view = latest_pressure(conn, ticker=normalized, price_source=source or services.price_source,
                               window_bars=window_bars, cmf_threshold=cmf_threshold, as_of=as_of)
    return json_response(view)


@router.get("/orders/{order_id}/chart")
def get_order_chart(order_id: UUID, services: ServicesDep) -> Response:
    with services.engine.connect() as conn:
        return json_response(order_chart(conn, order_id))
