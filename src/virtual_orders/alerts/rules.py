"""Pure alert rules over closed 1-minute bars (D26, D27). Close-to-close only: never infers an intrabar path."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from core.domain.models import Bar
from virtual_orders.analytics.pressure import PressureEstimate, PressureSide, estimate_pressure, strong_pressure

ABOVE = "ABOVE"
BELOW = "BELOW"


def _cooled(ts: datetime, last: datetime | None, cooldown: timedelta) -> bool:
    return last is None or ts - last >= cooldown


def price_crossings(
    bars: Sequence[Bar], *, level: Decimal, direction: str, cooldown: timedelta, last_alert_ts: datetime | None = None
) -> list[Bar]:
    """ABOVE: previous close < level <= close. BELOW: previous close > level >= close. The first bar has no previous."""
    if direction not in (ABOVE, BELOW):
        raise ValueError(f"unknown crossing direction: {direction}")
    crossings: list[Bar] = []
    previous: Decimal | None = None
    last = last_alert_ts
    for item in sorted(bars, key=lambda b: b.ts):
        if previous is not None:
            crossed = previous < level <= item.close if direction == ABOVE else previous > level >= item.close
            if crossed and _cooled(item.ts, last, cooldown):
                crossings.append(item)
                last = item.ts
        previous = item.close
    return crossings


def pressure_alert(
    bars: Sequence[Bar],
    *,
    cmf_threshold: Decimal,
    window_bars: int,
    cooldown: timedelta,
    last_alert_ts: datetime | None = None,
) -> tuple[PressureEstimate, PressureSide] | None:
    """Strong pressure over the latest `window_bars` bars; None without a full window or inside the cooldown."""
    window = sorted(bars, key=lambda b: b.ts)[-window_bars:]
    if len(window) < window_bars:
        return None
    estimate = estimate_pressure(window)
    if estimate is None:
        return None
    side = strong_pressure(estimate, cmf_threshold)
    if side is None or not _cooled(estimate.last_bar_ts, last_alert_ts, cooldown):
        return None
    return estimate, side
