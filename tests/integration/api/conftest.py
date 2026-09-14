"""API harness: the real app over real Postgres with fake providers and a controllable clock. No network."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from core.domain.models import FillConfig
from tests.integration.support import (
    API_KEY,
    CODE_VERSION,
    PRICE_SOURCE,
    SIGNAL_CREATED_AT,
    FakeBarSource,
    FakeDividends,
    FakeReference,
    FakeSplits,
    FakeTickerCheck,
    MutableClock,
    feeds,
)
from virtual_orders.api.app import create_app
from virtual_orders.services import Services


@dataclass
class ApiHarness:
    client: TestClient
    services: Services
    bars: FakeBarSource
    tickers: FakeTickerCheck
    clock: MutableClock


@pytest.fixture
def api(engine) -> Iterator[ApiHarness]:
    bars = FakeBarSource()
    tickers = FakeTickerCheck()
    clock = MutableClock(SIGNAL_CREATED_AT)
    services = Services(
        engine=engine, gateway=feeds(bars), price_source=PRICE_SOURCE, fill_config=FillConfig(),
        code_version=CODE_VERSION, ticker_check=tickers, dividend_primary=FakeDividends("fmp"),
        dividend_secondary=FakeDividends("yfinance"), split_source=FakeSplits(), reference=FakeReference(),
        api_key=API_KEY, eval_interval_minutes=2, bootstrap_resamples=200, bootstrap_seed=42,
        clock=clock, close=lambda: None,
    )
    with TestClient(create_app(services), headers={"X-API-Key": API_KEY}) as client:
        yield ApiHarness(client, services, bars, tickers, clock)
