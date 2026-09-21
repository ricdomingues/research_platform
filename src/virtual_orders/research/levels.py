"""Entry, stop and target levels from market structure and volatility (Plan 5, D80).

Deliberately **not** a model output. Prices come from things that exist on the chart — the pattern's own high
and low, the ATR, the nearest confirmed support or resistance — combined by a versioned, auditable policy. A
classifier may later score a candidate, but it is never allowed to invent the prices it is scored on.

The output is built to fit the platform's existing `SignalSpec` contract and is checked with the platform's own
`core.domain.validation.validate_signal`: a level chain the engine would reject is reported here as invalid,
never silently repaired and never sent to `/signals`.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Direction, SignalSpec
from core.domain.validation import validate_signal
from virtual_orders.research.market_structure import MarketStructure
from virtual_orders.research.models import ZERO, Candle, PatternDirection, quantize_ratio

LEVELS_VERSION = "levels-v1"
PRICE_TICK = Decimal("0.01")  # US equities quote in cents; every proposed level is a real, quotable price


@dataclass(frozen=True)
class LevelPolicy:
    """Versioned distances. Every number a proposed level depends on is named here, never inlined."""

    entry_zone_atr: Decimal = Decimal("0.25")
    stop_buffer_atr: Decimal = Decimal("0.25")
    target1_atr: Decimal = Decimal("2")
    target2_atr: Decimal = Decimal("3")
    min_risk_reward: Decimal = Decimal("1.5")

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{item.name} must be Decimal")
            if value <= ZERO:
                raise ValueError(f"{item.name} must be positive")
        if self.target2_atr <= self.target1_atr:
            raise ValueError("target2_atr must be beyond target1_atr")

    def snapshot(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


DEFAULT_POLICY = LevelPolicy()


def to_tick(value: Decimal) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        return value.quantize(PRICE_TICK, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class RiskLevels:
    """A complete, validated level chain, or an invalid one carrying the platform's own error codes."""

    levels_version: str
    policy: dict[str, Any]
    direction: Direction
    entry_zone_low: Decimal
    entry_zone_high: Decimal
    stop: Decimal
    target1: Decimal
    target2: Decimal | None
    risk: Decimal
    risk_reward: Decimal | None
    basis: dict[str, Any]
    valid: bool
    errors: tuple[str, ...]

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def signal_spec(self, ticker: str, *, valid_sessions: int, trigger_price: Decimal | None = None) -> SignalSpec:
        """The existing platform contract. Raises if the chain is invalid: an invalid chain never becomes a signal."""
        if not self.valid:
            raise ValueError(f"invalid level chain: {', '.join(self.errors)}")
        return SignalSpec(
            ticker=ticker, direction=self.direction, entry_zone_low=self.entry_zone_low,
            entry_zone_high=self.entry_zone_high, stop=self.stop, target1=self.target1, target2=self.target2,
            trigger_price=trigger_price, valid_sessions=valid_sessions,
        )


def _risk_reward(entry: Decimal, stop: Decimal, target: Decimal) -> Decimal | None:
    with localcontext(CANONICAL_CONTEXT):
        risk = abs(entry - stop)
        if risk == ZERO:
            return None
        return quantize_ratio(abs(target - entry) / risk)


def propose_levels(
    pattern_candles: list[Candle],
    *,
    direction: PatternDirection,
    atr: Decimal,
    structure: MarketStructure,
    policy: LevelPolicy = DEFAULT_POLICY,
) -> RiskLevels:
    """Levels for a detected pattern, from the pattern's own extremes, the ATR and the nearest confirmed level.

    Long side (the short side is the exact mirror):

    * the entry zone runs from the pattern's closing price up to its high, widened to at least
      `entry_zone_atr` x ATR, so an entry is never a single unreachable price;
    * the stop sits `stop_buffer_atr` x ATR below the pattern's low, and below the nearest confirmed support
      when one exists — the level that would invalidate the read, not a round number;
    * target1 is `target1_atr` x ATR above the entry, pulled back to the nearest confirmed resistance when that
      resistance sits in front of it, because the first realistic objective is where supply already showed;
    * target2 extends to `target2_atr` x ATR and is dropped when it would not clear target1.

    Every price is quantized to a real tick and the whole chain is then validated by the platform's own rule.
    """
    if not pattern_candles:
        raise ValueError("a level proposal needs at least one candle")
    if atr <= ZERO:
        raise ValueError("atr must be positive")
    if direction is PatternDirection.NEUTRAL:
        raise ValueError("a neutral pattern proposes no direction")
    long = direction is PatternDirection.BULLISH
    side = Direction.LONG if long else Direction.SHORT
    high = max(candle.high for candle in pattern_candles)
    low = min(candle.low for candle in pattern_candles)
    close = pattern_candles[-1].close

    capping_level: Decimal | None = None  # the confirmed level that pulled target1 back, whichever side
    widening_level: Decimal | None = None  # the confirmed level that pushed the stop out, whichever side
    with localcontext(CANONICAL_CONTEXT):
        zone_width = atr * policy.entry_zone_atr
        buffer = atr * policy.stop_buffer_atr
        if long:
            entry_high = high
            entry_low = min(close, high - zone_width)
            stop = low - buffer
            if structure.support is not None and structure.support < low:
                widened = min(stop, structure.support - buffer)
                if widened < stop:
                    stop, widening_level = widened, structure.support
            target1 = entry_high + atr * policy.target1_atr
            if structure.resistance is not None and entry_high < structure.resistance < target1:
                target1, capping_level = structure.resistance, structure.resistance
            target2 = entry_high + atr * policy.target2_atr
        else:
            entry_low = low
            entry_high = max(close, low + zone_width)
            stop = high + buffer
            if structure.resistance is not None and structure.resistance > high:
                widened = max(stop, structure.resistance + buffer)
                if widened > stop:
                    stop, widening_level = widened, structure.resistance
            target1 = entry_low - atr * policy.target1_atr
            if structure.support is not None and target1 < structure.support < entry_low:
                target1, capping_level = structure.support, structure.support
            target2 = entry_low - atr * policy.target2_atr

    entry_low, entry_high = to_tick(entry_low), to_tick(entry_high)
    stop, target1, target2 = to_tick(stop), to_tick(target1), to_tick(target2)
    reference = entry_high if long else entry_low
    if (target2 <= target1) if long else (target2 >= target1):
        optional_target2: Decimal | None = None  # a second target that does not clear the first is not a target
    else:
        optional_target2 = target2

    with localcontext(CANONICAL_CONTEXT):
        risk = abs(reference - stop)
    spec_errors: tuple[str, ...] = ()
    try:
        spec = SignalSpec(
            ticker="PROBE", direction=side, entry_zone_low=entry_low, entry_zone_high=entry_high, stop=stop,
            target1=target1, target2=optional_target2, valid_sessions=1,
        )
    except (TypeError, ValueError) as exc:  # a malformed chain is reported, never repaired
        spec_errors = (f"INVALID_LEVELS:{type(exc).__name__}",)
    else:
        spec_errors = tuple(validate_signal(spec))

    reward = _risk_reward(reference, stop, target1)
    if reward is not None and reward < policy.min_risk_reward:
        spec_errors = (*spec_errors, "RISK_REWARD_BELOW_MINIMUM")
    return RiskLevels(
        levels_version=LEVELS_VERSION,
        policy=policy.snapshot(),
        direction=side,
        entry_zone_low=entry_low,
        entry_zone_high=entry_high,
        stop=stop,
        target1=target1,
        target2=optional_target2,
        risk=risk,
        risk_reward=reward,
        basis={
            "pattern_high": high, "pattern_low": low, "pattern_close": close, "atr": atr,
            "support": structure.support, "resistance": structure.resistance,
            # Side-neutral by construction. The previous names ("resistance_capped_target1",
            # "support_widened_stop") described only the long side and were structurally unrecordable on the
            # short one, where support caps target1 and resistance widens the stop — so every short recorded
            # `false` however hard structure had moved its prices, and the clamp that decides reward was
            # invisible in the stored evidence. Recording which level acted, not merely that one did.
            "target1_capped_by_structure": capping_level is not None,
            "capping_level": None if capping_level is None else to_tick(capping_level),
            "stop_widened_by_structure": widening_level is not None,
            "widening_level": None if widening_level is None else to_tick(widening_level),
            "context_version": structure.context_version,
        },
        valid=not spec_errors,
        errors=spec_errors,
    )
