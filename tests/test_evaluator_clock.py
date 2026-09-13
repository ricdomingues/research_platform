from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from virtual_orders.evaluator.clock import require_aware


def test_require_aware_normalizes_to_utc():
    local = datetime(2025, 11, 25, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    result = require_aware(local, "market_now")
    assert result == datetime(2025, 11, 25, 15, 0, tzinfo=UTC)
    assert result.tzinfo is UTC


def test_require_aware_rejects_naive():
    with pytest.raises(ValueError, match="market_now must be timezone-aware"):
        require_aware(datetime(2025, 11, 25, 15, 0), "market_now")  # noqa: DTZ001
