"""API harness: the real app over real Postgres with fake providers and a controllable clock. No network."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.domain.models import FillConfig
from tests.integration.support import (
    CODE_VERSION,
    PRICE_SOURCE,
    SIGNAL_CREATED_AT,
    FakeBarSource,
    FakeDividends,
    FakeReference,
    FakeSplits,
    feeds,
)
from virtual_orders.api.app import create_app
from virtual_orders.marketdata.sources import SourceUnavailable, TickerStatus
from virtual_orders.services import Services

API_KEY = "test-api-key"


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def set(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeTickerCheck:
    name = "fake_assets"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.untradable: dict[str, str] = {}
        self.failing = False

    def check_ticker(self, ticker: str) -> TickerStatus:
        self.calls.append(ticker)
        if self.failing:
            raise SourceUnavailable("assets unavailable (fake)")
        reason = self.untradable.get(ticker)
        return TickerStatus(ticker, reason is None, reason)


@dataclass
class ApiHarness:
    client: TestClient
    services: Services
    bars: FakeBarSource
    tickers: FakeTickerCheck
    clock: MutableClock


def json_text(body: Any) -> str:
    """JSON with Decimals written as exact number tokens (never via float)."""
    numbers: dict[str, str] = {}

    def default(value: Any) -> str:
        if isinstance(value, Decimal):
            marker = f"__decimal_{len(numbers)}__"
            numbers[marker] = format(value, "f")
            return marker
        raise TypeError(f"not JSON serializable: {type(value).__name__}")

    text = json.dumps(body, default=default)
    for marker, number in numbers.items():
        text = text.replace(f'"{marker}"', number)
    return text


def post_json(client: TestClient, path: str, body: Any):
    return client.post(path, content=json_text(body), headers={"Content-Type": "application/json"})


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
