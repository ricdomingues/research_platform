"""Worker harness: real Postgres, fake providers, a controllable clock and a recording sink. No network, no sleep."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from core.domain.models import FillConfig
from tests.integration.alert_support import RecordingSink
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    FakeBarSource,
    FakeDividends,
    FakeReference,
    FakeSplits,
    FakeTickerCheck,
    MutableClock,
    feeds,
)
from tests.support import et
from virtual_orders.services import Services


@dataclass
class WorkerHarness:
    services: Services
    bars: FakeBarSource
    clock: MutableClock
    sink: RecordingSink
    closed: list[int]


@pytest.fixture
def worker(engine) -> Iterator[WorkerHarness]:
    bars = FakeBarSource()
    clock = MutableClock(et(DAY, "09:00"))
    sink = RecordingSink()
    closed: list[int] = []
    services = Services(
        engine=engine, gateway=feeds(bars), price_source=PRICE_SOURCE, fill_config=FillConfig(),
        code_version=CODE_VERSION, ticker_check=FakeTickerCheck(), dividend_primary=FakeDividends("fmp"),
        dividend_secondary=FakeDividends("yfinance"), split_source=FakeSplits(), reference=FakeReference(),
        api_key="unused", eval_interval_minutes=2, bootstrap_resamples=200, bootstrap_seed=42,
        clock=clock, close=lambda: closed.append(1), alert_sink=sink,
    )
    yield WorkerHarness(services, bars, clock, sink, closed)
