"""Signal level-chain validation (spec 3.8). Ticker tradability is checked by the API layer."""

from __future__ import annotations

from core.domain.models import ZERO, Direction, SignalSpec


def validate_signal(signal: SignalSpec) -> list[str]:
    errors: list[str] = []
    if not signal.ticker or signal.ticker != signal.ticker.strip().upper():
        errors.append("TICKER_INVALID")
    prices = {
        "ENTRY_ZONE_LOW": signal.entry_zone_low,
        "ENTRY_ZONE_HIGH": signal.entry_zone_high,
        "STOP": signal.stop,
        "TARGET1": signal.target1,
        "TARGET2": signal.target2,
        "TRIGGER_PRICE": signal.trigger_price,
    }
    for name, value in prices.items():
        if value is not None and value <= ZERO:
            errors.append(f"{name}_NOT_POSITIVE")
    if not 1 <= signal.valid_sessions <= 20:
        errors.append("VALID_SESSIONS_OUT_OF_RANGE")
    if errors:
        return errors

    s = signal
    if not s.entry_zone_low <= s.entry_zone_high:
        errors.append("ZONE_INVERTED")
    if s.direction is Direction.LONG:
        if not s.stop < s.entry_zone_low:
            errors.append("STOP_NOT_BEYOND_ZONE")
        if not s.entry_zone_high < s.target1:
            errors.append("TARGET1_NOT_BEYOND_ZONE")
        if s.target2 is not None and not s.target1 < s.target2:
            errors.append("TARGET2_NOT_BEYOND_TARGET1")
    else:
        if not s.stop > s.entry_zone_high:
            errors.append("STOP_NOT_BEYOND_ZONE")
        if not s.entry_zone_low > s.target1:
            errors.append("TARGET1_NOT_BEYOND_ZONE")
        if s.target2 is not None and not s.target1 > s.target2:
            errors.append("TARGET2_NOT_BEYOND_TARGET1")
    return errors
