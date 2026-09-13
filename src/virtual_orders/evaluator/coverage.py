"""Coverage policies for actionability (D7): a policy, not a domain truth.

STRICT_PRIMARY treats every expected minute without a bar in the order's price_source as unresolved, which
makes actionability unverifiable (503). A future policy may consult a secondary provider; it must then return
the sources and versions it used in `evidence` (persisted in the ACTIONABILITY run) and never substitute bars
silently. Actionability itself always runs on the primary price_source bars.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from core.domain.models import Bar


@dataclass(frozen=True)
class CoverageDecision:
    verified: bool
    unresolved: tuple[datetime, ...]
    evidence: dict[str, Any] = field(default_factory=dict)


class CoveragePolicy(Protocol):
    name: str

    def assess(
        self, *, price_source: str, ticker: str, expected: Sequence[datetime], bars: Sequence[Bar]
    ) -> CoverageDecision: ...


class StrictPrimaryCoverage:
    name = "STRICT_PRIMARY"

    def assess(
        self, *, price_source: str, ticker: str, expected: Sequence[datetime], bars: Sequence[Bar]
    ) -> CoverageDecision:
        present = {item.ts for item in bars}
        unresolved = tuple(minute for minute in expected if minute not in present)
        return CoverageDecision(not unresolved, unresolved)


STRICT_PRIMARY_COVERAGE = StrictPrimaryCoverage()
