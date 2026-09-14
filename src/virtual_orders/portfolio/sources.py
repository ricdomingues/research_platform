"""Read-only real portfolio contract (roadmap "portfólio real no dashboard", D46).

No implementation exists: a Robinhood read-only provider is gated by the roadmap Phase 0 (17 checks, none verified,
see docs/superpowers/roadmap/2026-09-14-robinhood-phase0-feasibility-report.md). Typed methods only: no order methods,
no generic tool passthrough.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

PHASE_0_PENDING = "PHASE_0_PENDING"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


@dataclass(frozen=True)
class RealPosition:
    ticker: str
    quantity: Decimal
    average_cost: Decimal | None
    market_value: Decimal | None
    as_of: datetime


class PortfolioSource(Protocol):
    name: str

    def list_positions(self) -> list[RealPosition]: ...
