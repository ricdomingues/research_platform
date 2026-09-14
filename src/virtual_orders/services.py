"""Dependencies the API (and later the worker) run on. Built only by `virtual_orders.bootstrap` or by tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Engine

from core.domain.models import FillConfig
from virtual_orders.marketdata.gateway import MarketDataGateway
from virtual_orders.marketdata.sources import DividendSource, ReferenceSource, SplitSource, TickerCheck


@dataclass(frozen=True)
class Services:
    engine: Engine
    gateway: MarketDataGateway
    price_source: str
    fill_config: FillConfig
    code_version: str
    ticker_check: TickerCheck
    dividend_primary: DividendSource
    dividend_secondary: DividendSource
    split_source: SplitSource
    reference: ReferenceSource
    api_key: str = field(repr=False)
    eval_interval_minutes: int
    bootstrap_resamples: int
    bootstrap_seed: int
    clock: Callable[[], datetime] = field(repr=False)
    close: Callable[[], None] = field(repr=False)
