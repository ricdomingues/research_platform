"""Composition root (Plan 2 close-out entry 1): the only module besides the adapters that imports them."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from sqlalchemy import Engine

from virtual_orders.api.app import create_app
from virtual_orders.config import ConfigError, Settings, load_settings
from virtual_orders.marketdata.alpaca import AlpacaAssets, AlpacaBars, AlpacaSplits
from virtual_orders.marketdata.fmp import FmpDividends
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.yfinance_source import YFinanceSource
from virtual_orders.notify.n8n import N8nWebhook
from virtual_orders.services import Services
from virtual_orders.storage.database import make_engine

HTTP_TIMEOUT_SECONDS = 10.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_services(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    engine: Engine | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Services:
    owns_client = http_client is None
    client = http_client if http_client is not None else httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)

    def release_client() -> None:
        if owns_client:
            client.close()

    gateway = MarketDataGateway([AlpacaBars(client, settings.alpaca_api_key, settings.alpaca_secret_key)])
    if settings.price_source not in gateway.source_ids:
        release_client()
        raise ConfigError([
            f"UNKNOWN_PRICE_SOURCE:PRICE_SOURCE (registered: {', '.join(gateway.source_ids)})",
        ])
    owns_engine = engine is None
    try:
        database = engine if engine is not None else make_engine(settings.database_url)
    except Exception:
        release_client()  # Plan 3A close-out entry 12: never leak the owned client on a failed startup
        raise

    def close() -> None:
        release_client()
        if owns_engine:
            database.dispose()

    yfinance = YFinanceSource()
    alert_sink = None if settings.n8n_webhook_url is None else N8nWebhook(client, settings.n8n_webhook_url)
    return Services(
        engine=database,
        gateway=gateway,
        price_source=settings.price_source,
        fill_config=settings.fill_config,
        code_version=settings.code_version,
        ticker_check=AlpacaAssets(client, settings.alpaca_api_key, settings.alpaca_secret_key,
                                  base_url=settings.alpaca_trading_url),
        dividend_primary=FmpDividends(client, settings.fmp_api_key),
        dividend_secondary=yfinance,
        split_source=AlpacaSplits(client, settings.alpaca_api_key, settings.alpaca_secret_key),
        reference=yfinance,
        api_key=settings.api_key,
        eval_interval_minutes=settings.eval_interval_minutes,
        bootstrap_resamples=settings.bootstrap_resamples,
        bootstrap_seed=settings.bootstrap_seed,
        clock=clock or _utc_now,
        close=close,
        alert_sink=alert_sink,
        portfolio_source=None,  # D46: no adapter until the roadmap Phase 0 report approves one
    )


def app_from_environment() -> FastAPI:
    """ASGI factory: `uvicorn virtual_orders.bootstrap:app_from_environment --factory`."""
    return create_app(build_services(load_settings(os.environ)))
