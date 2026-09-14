from datetime import UTC, date, datetime, timedelta

import pytest

from virtual_orders.alerts.outbox import backoff, review_session_day

RECORDED = datetime(2025, 11, 26, 2, 0, tzinfo=UTC)  # 21:00 ET on 2025-11-25


@pytest.mark.parametrize("ref, expected", [
    ("2025-11-24", date(2025, 11, 24)),  # DAILY_RANGE_MISMATCH / DIVIDEND_* refs are session dates
    ("2025-11-25T15:10:00+00:00", date(2025, 11, 25)),  # MISSING_BAR_* refs are minute instants
    ("2025-11-26T01:30:00+00:00", date(2025, 11, 25)),  # 20:30 ET still belongs to the ET day
    ("2025-11-25T10:10:00", date(2025, 11, 25)),  # a naive instant falls back to the recorded ET day
    ("look", date(2025, 11, 25)),  # free-form manual refs fall back to the recorded ET day
])
def test_review_session_day(ref, expected):
    assert review_session_day(ref, RECORDED) == expected


def test_backoff_doubles_from_one_minute_and_caps_at_thirty():
    assert [backoff(n) for n in range(8)] == [timedelta(0)] + [
        timedelta(minutes=m) for m in (1, 2, 4, 8, 16, 30, 30)
    ]
