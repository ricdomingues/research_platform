"""Strategy metrics over closed orders (spec 5.4). Review policy never mixes flagged trades silently."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from core.domain.models import Direction, OrderState, OrderStatus
from core.metrics.resampling import (
    bootstrap_ci, drawdown_permutation_percentiles, max_drawdown, mean_stat, win_rate_stat,
)

MIN_SAMPLE = 30
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
SHORT_BORROW_NOT_SIMULATED = "SHORT_BORROW_NOT_SIMULATED"


@dataclass(frozen=True)
class TradeResult:
    order_id: str
    direction: Direction
    opened_at: datetime
    closed_at: datetime
    r_multiple: Decimal
    mfe_r: Decimal | None = None
    mae_r: Decimal | None = None
    review_reasons: tuple[str, ...] = ()

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)


@dataclass(frozen=True)
class ExecutionCounts:
    filled: int
    resolved: int

    @property
    def rate(self) -> float | None:
        return None if self.resolved == 0 else self.filled / self.resolved


def execution_counts(orders: Iterable[OrderState]) -> ExecutionCounts:
    filled = 0
    resolved = 0
    for state in orders:
        if state.opened_at is not None:
            filled += 1
            resolved += 1
        elif not state.frozen and state.status in (OrderStatus.EXPIRED, OrderStatus.INVALIDATED):
            resolved += 1
    return ExecutionCounts(filled, resolved)


@dataclass(frozen=True)
class ReviewTally:
    count: int
    reasons: dict[str, int]


@dataclass(frozen=True)
class MetricsSummary:
    trades: int
    win_rate: float | None
    avg_r: float | None
    expectancy_r: float | None
    profit_factor: float | None
    max_drawdown_r: float
    avg_duration_seconds: float | None
    avg_mfe_r: float | None
    avg_mae_r: float | None
    execution_rate: float | None
    win_rate_ci: tuple[float, float] | None
    expectancy_ci: tuple[float, float] | None
    drawdown_sequence_risk: dict[str, float] | None
    excluded_needs_review: ReviewTally
    included_needs_review: ReviewTally
    warnings: tuple[str, ...]


def _tally(trades: list[TradeResult]) -> ReviewTally:
    counter = Counter(reason for t in trades for reason in t.review_reasons)
    return ReviewTally(len(trades), dict(sorted(counter.items())))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(
    trades: Iterable[TradeResult],
    counts: ExecutionCounts,
    include_needs_review: bool = False,
    resamples: int = 2000,
    seed: int = 42,
) -> MetricsSummary:
    ordered = sorted(trades, key=lambda t: (t.closed_at, t.order_id))
    flagged = [t for t in ordered if t.needs_review]
    used = ordered if include_needs_review else [t for t in ordered if not t.needs_review]
    empty = ReviewTally(0, {})

    rs = [float(t.r_multiple) for t in used]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    avg_r = _mean(rs)

    warnings: list[str] = []
    if len(rs) < MIN_SAMPLE:
        warnings.append(INSUFFICIENT_SAMPLE)
    if any(t.direction is Direction.SHORT for t in used):
        warnings.append(SHORT_BORROW_NOT_SIMULATED)

    return MetricsSummary(
        trades=len(rs),
        win_rate=len(wins) / len(rs) if rs else None,
        avg_r=avg_r,
        expectancy_r=avg_r,
        profit_factor=sum(wins) / abs(sum(losses)) if losses else None,
        max_drawdown_r=max_drawdown(rs),
        avg_duration_seconds=_mean([(t.closed_at - t.opened_at).total_seconds() for t in used]),
        avg_mfe_r=_mean([float(t.mfe_r) for t in used if t.mfe_r is not None]),
        avg_mae_r=_mean([float(t.mae_r) for t in used if t.mae_r is not None]),
        execution_rate=counts.rate,
        win_rate_ci=bootstrap_ci(rs, win_rate_stat, resamples, seed),
        expectancy_ci=bootstrap_ci(rs, mean_stat, resamples, seed),
        drawdown_sequence_risk=drawdown_permutation_percentiles(rs, resamples, seed),
        excluded_needs_review=empty if include_needs_review else _tally(flagged),
        included_needs_review=_tally(flagged) if include_needs_review else empty,
        warnings=tuple(warnings),
    )
