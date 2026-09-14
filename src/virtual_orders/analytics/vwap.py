"""Session-anchored VWAP for charts (D43). Pure and deterministic; not the pressure window VWAP (D27)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from zoneinfo import ZoneInfo

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Bar

METHOD = "SESSION_VWAP_TYPICAL_PRICE_V1"
QUANTUM = Decimal("0.0001")
ZERO = Decimal(0)
THREE = Decimal(3)
_MARKET_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class VwapPoint:
    ts: datetime
    value: Decimal


def session_vwap(bars: Sequence[Bar]) -> list[VwapPoint]:
    """Running sum(typical price * volume) / sum(volume), restarted at each ET date; the close while volume is zero."""
    ordered = sorted(bars, key=lambda item: item.ts)
    if len({item.ts for item in ordered}) != len(ordered):
        raise ValueError("duplicate bar minute")
    points: list[VwapPoint] = []
    session: date | None = None
    flow = volume = ZERO
    with localcontext(CANONICAL_CONTEXT):
        for item in ordered:
            day = item.ts.astimezone(_MARKET_TZ).date()
            if day != session:
                session, flow, volume = day, ZERO, ZERO
            flow += (item.high + item.low + item.close) / THREE * item.volume
            volume += item.volume
            value = item.close if volume == 0 else flow / volume
            points.append(VwapPoint(item.ts, value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)))
    return points
