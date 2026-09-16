"""Pure statistics for the daily paper-observation report (Plan 4, D64-D68). No I/O; Decimal only, never float."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum

from core.domain.hashing import CANONICAL_CONTEXT
from virtual_orders.analytics.pressure import PressureEstimate, PressureSide, strong_pressure

R_QUANTUM = Decimal("0.0001")
RATE_QUANTUM = Decimal("0.0001")
PCT_QUANTUM = Decimal("0.01")
OBSERVATION_CMF_THRESHOLD = Decimal("0.05")
STRONG_CMF = Decimal("0.15")
MIN_TRADES_FOR_BUCKET_MEAN = 5
UNKNOWN_STATE = "UNKNOWN"
ASSOCIATION_NOTE = (
    "Descriptive counts over a small paper sample: not causal evidence, not a trading signal, and never used by any "
    "evaluation. A bucket mean is shown only with at least 5 trades; n is always shown beside it."
)
_DIRECTIONS = frozenset({"LONG", "SHORT"})


def whole_seconds(start: datetime, end: datetime) -> int:
    """Floor of (end - start) in whole seconds."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    delta = end - start
    return delta.days * 86400 + delta.seconds


def nearest_rank(values: Sequence[int], percentile: int) -> int | None:
    """The ceil(p/100 * n)-th smallest value, without interpolation (D65); None when there is no value."""
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    if not values:
        return None
    ordered = sorted(values)
    rank = -(-percentile * len(ordered) // 100)
    return ordered[rank - 1]


@dataclass(frozen=True)
class LatencyStats:
    count: int
    median_seconds: int | None
    p90_seconds: int | None
    max_seconds: int | None


def latency_stats(values: Sequence[int]) -> LatencyStats:
    return LatencyStats(len(values), nearest_rank(values, 50), nearest_rank(values, 90),
                        max(values) if values else None)


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    with localcontext(CANONICAL_CONTEXT):
        result = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    return result if result != 0 else Decimal(0).quantize(quantum)  # never "-0.0000"


def quantize_r(value: Decimal) -> Decimal:
    return _quantize(value, R_QUANTUM)


def ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return _quantize(Decimal(numerator) / Decimal(denominator), RATE_QUANTUM)


def coverage_pct(expected: int, missing: int) -> Decimal | None:
    if expected <= 0:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return _quantize(Decimal(expected - missing) * 100 / Decimal(expected), PCT_QUANTUM)


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    with localcontext(CANONICAL_CONTEXT):
        return quantize_r(sum(values, Decimal(0)) / len(values))


def _median(values: Sequence[Decimal]) -> Decimal | None:
    """Middle value; the mean of the two middle values when the count is even (D64)."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return quantize_r(ordered[middle])
    with localcontext(CANONICAL_CONTEXT):
        return quantize_r((ordered[middle - 1] + ordered[middle]) / 2)


@dataclass(frozen=True)
class TradeStats:
    trades: int
    wins: int
    losses: int
    win_rate: Decimal | None
    sum_r: Decimal
    mean_r: Decimal | None
    mean_mfe_r: Decimal | None
    median_mfe_r: Decimal | None
    mean_mae_r: Decimal | None
    median_mae_r: Decimal | None


def trade_stats(results: Sequence[tuple[Decimal, Decimal | None, Decimal | None]]) -> TradeStats:
    """`results` = (r_multiple, mfe_r, mae_r) per closed trade. Wins are r > 0, losses r < 0 (spec 5.4)."""
    r_values = [r for r, _, _ in results]
    mfe = [value for _, value, _ in results if value is not None]
    mae = [value for _, _, value in results if value is not None]
    wins = sum(1 for r in r_values if r > 0)
    with localcontext(CANONICAL_CONTEXT):
        total = sum(r_values, Decimal(0))
    return TradeStats(
        trades=len(r_values), wins=wins, losses=sum(1 for r in r_values if r < 0),
        win_rate=ratio(wins, len(r_values)), sum_r=quantize_r(total), mean_r=_mean(r_values),
        mean_mfe_r=_mean(mfe), median_mfe_r=_median(mfe), mean_mae_r=_mean(mae), median_mae_r=_median(mae),
    )


@dataclass(frozen=True)
class StateChange:
    at: datetime
    state: str


def seconds_by_state(
    initial: str | None, changes: Sequence[StateChange], start: datetime, end: datetime
) -> dict[str, int]:
    """Seconds spent in each health state inside [start, end); `initial` is the state in force at `start` (D68)."""
    if end <= start:
        return {}
    totals: dict[str, int] = {}
    current, since = initial or UNKNOWN_STATE, start
    for change in sorted(changes, key=lambda item: item.at):
        if not start <= change.at < end:
            raise ValueError("state change outside the window")
        totals[current] = totals.get(current, 0) + whole_seconds(since, change.at)
        current, since = change.state, change.at
    totals[current] = totals.get(current, 0) + whole_seconds(since, end)
    return {state: seconds for state, seconds in sorted(totals.items()) if seconds > 0}


class Alignment(StrEnum):
    ALIGNED = "ALIGNED"
    OPPOSED = "OPPOSED"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"


class Strength(StrEnum):
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


def classify_pressure(estimate: PressureEstimate | None, direction: str) -> tuple[Alignment, Strength | None]:
    """D66: the strong side (D37 rule at CMF 0.05) against the trade direction, and |CMF| as the strength."""
    if direction not in _DIRECTIONS:
        raise ValueError(f"unknown direction {direction!r}")
    if estimate is None:
        return Alignment.UNAVAILABLE, None
    magnitude = abs(estimate.chaikin_money_flow)
    if magnitude >= STRONG_CMF:
        strength = Strength.STRONG
    elif magnitude >= OBSERVATION_CMF_THRESHOLD:
        strength = Strength.MODERATE
    else:
        strength = Strength.WEAK
    side = strong_pressure(estimate, OBSERVATION_CMF_THRESHOLD)
    if side is None:
        return Alignment.NEUTRAL, strength
    favourable = PressureSide.BUY if direction == "LONG" else PressureSide.SELL
    return (Alignment.ALIGNED if side is favourable else Alignment.OPPOSED), strength


@dataclass(frozen=True)
class PressureBucket:
    alignment: Alignment
    strength: Strength | None
    trades: int
    wins: int
    sum_r: Decimal
    mean_r: Decimal | None


_ALIGNMENT_ORDER = {item: index for index, item in enumerate(Alignment)}
_STRENGTH_MEMBERS: list[Strength] = list(Strength)
_STRENGTH_ORDER: dict[Strength | None, int] = (
    {item: index for index, item in enumerate(_STRENGTH_MEMBERS)} | {None: -1}
)


def pressure_buckets(items: Iterable[tuple[Alignment, Strength | None, Decimal]]) -> list[PressureBucket]:
    """D66: counts and sum of R per bucket; the mean only with at least MIN_TRADES_FOR_BUCKET_MEAN trades."""
    groups: dict[tuple[Alignment, Strength | None], list[Decimal]] = {}
    for alignment, strength, r_multiple in items:
        groups.setdefault((alignment, strength), []).append(r_multiple)
    buckets: list[PressureBucket] = []
    for (alignment, strength), values in sorted(
        groups.items(), key=lambda entry: (_ALIGNMENT_ORDER[entry[0][0]], _STRENGTH_ORDER[entry[0][1]])
    ):
        with localcontext(CANONICAL_CONTEXT):
            total = sum(values, Decimal(0))
        mean = _mean(values) if len(values) >= MIN_TRADES_FOR_BUCKET_MEAN else None
        buckets.append(PressureBucket(alignment, strength, len(values), sum(1 for r in values if r > 0),
                                      quantize_r(total), mean))
    return buckets


def failure_code(message: object) -> str:
    """D67: a stored failure message reduced to a fixed code; the message itself never leaves the database."""
    text = str(message)
    if text.startswith("UNKNOWN_DATA_SOURCE"):
        return "UNKNOWN_DATA_SOURCE"
    if text.startswith("ERROR:"):
        return "UNEXPECTED_ERROR"
    return "SOURCE_ERROR"
