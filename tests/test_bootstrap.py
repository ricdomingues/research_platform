from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from core.domain.models import FillConfig
from tests.config_support import BASE_ENV
from virtual_orders.bootstrap import build_services
from virtual_orders.config import DEFAULT_PRICE_SOURCE, ConfigError, load_settings
from virtual_orders.marketdata.alpaca import ALPACA_IEX_SOURCE, AlpacaAssets, AlpacaBars, AlpacaSplits
from virtual_orders.marketdata.fmp import FmpDividends
from virtual_orders.marketdata.gateway import UnknownDataSource
from virtual_orders.marketdata.yfinance_source import YFinanceSource


def settings(**overrides):
    return replace(load_settings(BASE_ENV), **overrides)


def offline_client(calls):
    return httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)))


def test_composition_pins_the_configured_price_source_without_fallback():
    calls = []
    services = build_services(settings(), http_client=offline_client(calls))
    try:
        assert services.gateway.source_ids == (ALPACA_IEX_SOURCE,)
        assert services.price_source == ALPACA_IEX_SOURCE == DEFAULT_PRICE_SOURCE
        assert isinstance(services.gateway.bar_source(ALPACA_IEX_SOURCE), AlpacaBars)
        with pytest.raises(UnknownDataSource):
            services.gateway.bar_source("yfinance")
        assert isinstance(services.ticker_check, AlpacaAssets)
        assert isinstance(services.dividend_primary, FmpDividends)
        assert isinstance(services.dividend_secondary, YFinanceSource)
        assert isinstance(services.split_source, AlpacaSplits)
        assert isinstance(services.reference, YFinanceSource)
        assert services.fill_config == FillConfig() and services.code_version == "unknown"
        assert services.clock().tzinfo is UTC
    finally:
        services.close()
    assert calls == []


def test_unknown_price_source_fails_at_startup():
    with pytest.raises(ConfigError, match="UNKNOWN_PRICE_SOURCE:PRICE_SOURCE"):
        build_services(settings(price_source="fake_feed"), http_client=offline_client([]))


def test_injected_client_and_clock_are_used_and_the_client_is_left_open():
    client = offline_client([])
    fixed = datetime(2025, 11, 25, 15, 0, tzinfo=UTC)
    services = build_services(settings(), http_client=client, clock=lambda: fixed)
    services.close()
    assert services.clock() == fixed
    assert not client.is_closed
    client.close()


def test_services_repr_hides_the_api_key():
    services = build_services(settings(api_key="very-secret"), http_client=offline_client([]))
    try:
        assert "very-secret" not in repr(services)
    finally:
        services.close()
