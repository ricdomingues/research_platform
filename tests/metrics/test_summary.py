from datetime import timedelta

import pytest

from core.domain.models import Direction, OrderState, OrderStatus
from core.metrics.summary import (
    INSUFFICIENT_SAMPLE, SHORT_BORROW_NOT_SIMULATED, ExecutionCounts, ReviewTally, TradeResult,
    execution_counts, summarize,
)
from tests.support import D, et

BASE = et("2025-11-25", "10:00")


def trade(i, r, direction=Direction.LONG, reasons=(), mfe=None, mae=None):
    closed = BASE + timedelta(hours=i + 1)
    return TradeResult(f"o{i}", direction, closed - timedelta(hours=1), closed, D(r),
                       None if mfe is None else D(mfe), None if mae is None else D(mae), reasons)


SAMPLE = [trade(0, 1, mfe=1.5, mae=-0.2), trade(1, -1, mfe=0.3, mae=-1), trade(2, 2, mfe=2.5, mae=-0.4),
          trade(3, -0.5, mfe=0.1, mae=-0.6)]


def test_hand_computed_summary():
    summary = summarize(SAMPLE, ExecutionCounts(filled=4, resolved=5))
    assert summary.trades == 4
    assert summary.win_rate == pytest.approx(0.5)
    assert summary.avg_r == pytest.approx(0.375)
    assert summary.expectancy_r == pytest.approx(0.375)
    assert summary.profit_factor == pytest.approx(2.0)
    assert summary.max_drawdown_r == pytest.approx(1.0)
    assert summary.avg_duration_seconds == pytest.approx(3600.0)
    assert summary.avg_mfe_r == pytest.approx(1.1)
    assert summary.avg_mae_r == pytest.approx(-0.55)
    assert summary.execution_rate == pytest.approx(0.8)
    assert summary.warnings == (INSUFFICIENT_SAMPLE,)
    assert summary.win_rate_ci is not None and summary.expectancy_ci is not None
    assert set(summary.drawdown_sequence_risk) == {"p5", "p50", "p95"}


def test_order_of_input_does_not_matter_and_is_reproducible():
    counts = ExecutionCounts(4, 4)
    assert summarize(list(reversed(SAMPLE)), counts) == summarize(SAMPLE, counts)


def test_needs_review_excluded_by_default_and_reported():
    flagged = trade(4, 5, reasons=("MISSING_BAR_LEVEL_TOUCH", "SPLIT"))
    default = summarize(SAMPLE + [flagged], ExecutionCounts(5, 5))
    assert default.trades == 4
    assert default.excluded_needs_review == ReviewTally(1, {"MISSING_BAR_LEVEL_TOUCH": 1, "SPLIT": 1})
    assert default.included_needs_review == ReviewTally(0, {})
    included = summarize(SAMPLE + [flagged], ExecutionCounts(5, 5), include_needs_review=True)
    assert included.trades == 5
    assert included.included_needs_review.count == 1
    assert included.excluded_needs_review == ReviewTally(0, {})


def test_short_warning_and_empty_summary():
    with_short = summarize([trade(0, 1, Direction.SHORT)], ExecutionCounts(1, 1))
    assert SHORT_BORROW_NOT_SIMULATED in with_short.warnings
    empty = summarize([], ExecutionCounts(0, 0))
    assert empty.trades == 0 and empty.win_rate is None and empty.profit_factor is None
    assert empty.max_drawdown_r == 0.0 and empty.execution_rate is None
    assert empty.win_rate_ci is None and empty.drawdown_sequence_risk is None
    assert empty.warnings == (INSUFFICIENT_SAMPLE,)


def test_execution_counts():
    filled_at = et("2025-11-25", "10:00")
    orders = [
        OrderState(status=OrderStatus.CLOSED, opened_at=filled_at),
        OrderState(status=OrderStatus.CANCELED, opened_at=filled_at),
        OrderState(status=OrderStatus.EXPIRED),
        OrderState(status=OrderStatus.INVALIDATED),
        OrderState(status=OrderStatus.PENDING),
        OrderState(status=OrderStatus.CANCELED),
        OrderState(status=OrderStatus.PENDING, frozen=True),
        OrderState(status=OrderStatus.EXPIRED, frozen=True),
    ]
    counts = execution_counts(orders)
    assert counts == ExecutionCounts(filled=2, resolved=4)
    assert counts.rate == pytest.approx(0.5)
