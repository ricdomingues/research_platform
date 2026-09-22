"""Outcome labels for a research candidate (Plan 5, D79). The only module allowed to read candles *after* a signal.

Features answer "what did the engine know at the signal"; labels answer "what happened next". Keeping them in
separate modules, each taking its own explicit slice of candles, is what makes an accidental leak hard to write:
a labeller is handed `future` and can physically not see the history, while a feature builder is handed the
history and can physically not see the future.

**Same-candle ambiguity.** When one candle's range contains both the target and the stop, the intrabar path is
unknown. This module never assumes the favourable order: it records the stop, exactly as `fill_model v1` does
for real virtual orders (spec 4.4, "stop and target in the same candle: stop first"), and flags the outcome
`ambiguous` so research can count how often the result rests on that convention. The policy is versioned:
changing it changes `AMBIGUITY_POLICY` and therefore every stored label's identity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any

from core.domain.hashing import CANONICAL_CONTEXT
from core.domain.models import Direction
from virtual_orders.research.models import ZERO, Candle, quantize_ratio, quantized

LABEL_VERSION = "labels-v1"
AMBIGUITY_POLICY = "STOP_FIRST_ON_SAME_CANDLE_V1"
PCT_QUANTUM = Decimal("0.0001")
R_QUANTUM = Decimal("0.0001")
HUNDRED = Decimal(100)
DEFAULT_HORIZONS = (5, 10, 20)
DEFAULT_TARGET_ATR = Decimal("2")
DEFAULT_STOP_ATR = Decimal("1")


class BarrierResult(StrEnum):
    TARGET = "TARGET"
    STOP = "STOP"
    TIMEOUT = "TIMEOUT"
    NO_DATA = "NO_DATA"


def _favourable(direction: Direction, candle: Candle) -> Decimal:
    return candle.high if direction is Direction.LONG else candle.low


def _adverse(direction: Direction, candle: Candle) -> Decimal:
    return candle.low if direction is Direction.LONG else candle.high


def _move_pct(direction: Direction, entry: Decimal, price: Decimal) -> Decimal:
    """Signed move in percent, from the trade's point of view: positive is favourable for either direction."""
    with localcontext(CANONICAL_CONTEXT):
        raw = (price - entry) / entry * HUNDRED
        return quantized(raw if direction is Direction.LONG else -raw, PCT_QUANTUM)


@dataclass(frozen=True)
class HorizonOutcome:
    """What a fixed number of candles later looked like. `complete` is false when the series ran out first."""

    label_version: str
    horizon_candles: int
    candles_used: int
    complete: bool
    entry: Decimal
    exit_price: Decimal | None
    return_pct: Decimal | None
    mfe_pct: Decimal | None
    mae_pct: Decimal | None

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def fixed_horizon(
    future: Sequence[Candle], *, entry: Decimal, direction: Direction, horizon: int
) -> HorizonOutcome:
    """Return, MFE and MAE over the next `horizon` candles, measured from `entry`.

    `future` must start at the first candle **after** the signal candle. A short series is labelled
    `complete=False` rather than padded, so an unfinished window can be excluded from statistics instead of
    quietly counting as a small move.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if entry <= ZERO:
        raise ValueError("entry must be positive")
    window = list(future[:horizon])
    if not window:
        return HorizonOutcome(LABEL_VERSION, horizon, 0, False, entry, None, None, None, None)
    # Favourable and adverse are read from the trade's side: a long's best is the highest high and its worst the
    # lowest low, a short's the exact mirror.
    if direction is Direction.LONG:
        best = max(_favourable(direction, candle) for candle in window)
        worst = min(_adverse(direction, candle) for candle in window)
    else:
        best = min(_favourable(direction, candle) for candle in window)
        worst = max(_adverse(direction, candle) for candle in window)
    return HorizonOutcome(
        label_version=LABEL_VERSION,
        horizon_candles=horizon,
        candles_used=len(window),
        complete=len(window) == horizon,
        entry=entry,
        exit_price=window[-1].close,
        return_pct=_move_pct(direction, entry, window[-1].close),
        mfe_pct=_move_pct(direction, entry, best),
        mae_pct=_move_pct(direction, entry, worst),
    )


@dataclass(frozen=True)
class BarrierOutcome:
    """Which barrier was touched first, under an explicit, versioned same-candle policy."""

    label_version: str
    ambiguity_policy: str
    result: BarrierResult
    entry: Decimal
    stop: Decimal
    target: Decimal
    exit_price: Decimal | None
    candles_to_outcome: int | None
    candles_used: int
    complete: bool
    r_multiple: Decimal | None
    mfe_r: Decimal | None
    mae_r: Decimal | None
    ambiguous: bool
    gap_through: bool

    @property
    def target_first(self) -> bool | None:
        """The operational label an ML model predicts: True on TARGET, False on STOP, None when undecided."""
        if self.result is BarrierResult.TARGET:
            return True
        if self.result is BarrierResult.STOP:
            return False
        return None

    def as_document(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def atr_barriers(
    entry: Decimal,
    atr: Decimal,
    direction: Direction,
    *,
    target_atr: Decimal = DEFAULT_TARGET_ATR,
    stop_atr: Decimal = DEFAULT_STOP_ATR,
) -> tuple[Decimal, Decimal]:
    """(stop, target) placed a multiple of ATR away from `entry`, on the correct side of the trade."""
    if atr <= ZERO:
        raise ValueError("atr must be positive")
    with localcontext(CANONICAL_CONTEXT):
        if direction is Direction.LONG:
            return entry - atr * stop_atr, entry + atr * target_atr
        return entry + atr * stop_atr, entry - atr * target_atr


def target_before_stop(
    future: Sequence[Candle],
    *,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    direction: Direction,
    max_candles: int,
) -> BarrierOutcome:
    """Walk forward candle by candle until a barrier is touched, or `max_candles` run out.

    A touch is `high >= target` / `low <= stop` for a long (mirrored for a short), the same "a target only needs
    to be touched" rule the fill engine uses. A candle that opens beyond a barrier is filled at its open, never
    at the barrier price, so a gap through a level is recorded as the worse real price rather than a free fill.
    """
    if max_candles < 1:
        raise ValueError("max_candles must be >= 1")
    if entry <= ZERO:
        raise ValueError("entry must be positive")
    long = direction is Direction.LONG
    if long and not stop < entry < target:
        raise ValueError("a long needs stop < entry < target")
    if not long and not target < entry < stop:
        raise ValueError("a short needs target < entry < stop")
    with localcontext(CANONICAL_CONTEXT):
        risk = abs(entry - stop)
    window = list(future[:max_candles])
    if not window:
        return BarrierOutcome(LABEL_VERSION, AMBIGUITY_POLICY, BarrierResult.NO_DATA, entry, stop, target, None,
                              None, 0, False, None, None, None, False, False)

    best = worst = entry
    for offset, candle in enumerate(window, start=1):
        hit_target = candle.high >= target if long else candle.low <= target
        hit_stop = candle.low <= stop if long else candle.high >= stop
        worst = min(worst, candle.low) if long else max(worst, candle.high)
        if not hit_stop:
            # Spec v1.2 D3, the platform's own rule: in a candle where the stop triggers, that candle's
            # favourable extreme does NOT enter the MFE (the intrabar path is unknown, and the stop may have
            # come first), while its adverse extreme still enters the MAE. Research excursions are measured
            # exactly as `fill_model v1` measures them, so the two are comparable.
            best = max(best, candle.high) if long else min(best, candle.low)
        if not (hit_target or hit_stop):
            continue
        ambiguous = hit_target and hit_stop
        # The stop wins a shared candle (AMBIGUITY_POLICY): the intrabar path is unknown and this is the
        # conservative reading, identical to fill_model v1.
        if hit_stop:
            gapped = candle.open < stop if long else candle.open > stop
            exit_price = candle.open if gapped else stop
            result = BarrierResult.STOP
        else:
            gapped = candle.open > target if long else candle.open < target
            exit_price = candle.open if gapped else target
            result = BarrierResult.TARGET
        with localcontext(CANONICAL_CONTEXT):
            move = (exit_price - entry) if long else (entry - exit_price)
            r_multiple = quantized(move / risk, R_QUANTUM)
        return BarrierOutcome(
            label_version=LABEL_VERSION, ambiguity_policy=AMBIGUITY_POLICY, result=result, entry=entry, stop=stop,
            target=target, exit_price=exit_price, candles_to_outcome=offset, candles_used=offset, complete=True,
            r_multiple=r_multiple, mfe_r=_excursion_r(best, entry, risk, long),
            mae_r=_excursion_r(worst, entry, risk, long), ambiguous=ambiguous, gap_through=gapped,
        )

    complete = len(window) == max_candles
    with localcontext(CANONICAL_CONTEXT):
        move = (window[-1].close - entry) if long else (entry - window[-1].close)
        r_multiple = quantized(move / risk, R_QUANTUM)
    return BarrierOutcome(
        label_version=LABEL_VERSION, ambiguity_policy=AMBIGUITY_POLICY, result=BarrierResult.TIMEOUT, entry=entry,
        stop=stop, target=target, exit_price=window[-1].close, candles_to_outcome=None, candles_used=len(window),
        complete=complete, r_multiple=r_multiple, mfe_r=_excursion_r(best, entry, risk, long),
        mae_r=_excursion_r(worst, entry, risk, long), ambiguous=False, gap_through=False,
    )


def _excursion_r(price: Decimal, entry: Decimal, risk: Decimal, long: bool) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        move = (price - entry) if long else (entry - price)
        return quantize_ratio(move / risk)
