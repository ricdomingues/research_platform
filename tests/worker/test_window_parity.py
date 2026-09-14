"""D50 (M3): the neutral modules duplicate worker constants on purpose; these pins keep the copies equal."""

from datetime import date, datetime, time, timedelta

import pytest

from tests.support import et
from virtual_orders.alerts import outbox
from virtual_orders.alerts.watch import WATCH_GRACE, watch_session
from virtual_orders.analytics import vwap
from virtual_orders.readmodels import health, market
from virtual_orders.worker.schedule import LIVE_WINDOW_GRACE, MARKET_TZ, build_schedule, live_session


@pytest.mark.parametrize("day, hm", [
    ("2025-11-25", "09:29"), ("2025-11-25", "09:30"), ("2025-11-25", "16:05"), ("2025-11-25", "16:06"),
    ("2025-11-28", "13:05"), ("2025-11-28", "13:06"), ("2025-11-27", "11:00"), ("2025-11-29", "11:00"),
])
def test_watchlist_and_live_cycle_share_the_session_window(day, hm):
    now = et(day, hm)
    assert watch_session(now) == live_session(now)


def test_duplicated_window_constants_stay_equal():
    assert WATCH_GRACE == LIVE_WINDOW_GRACE
    assert health._MARKET_TZ == outbox._MARKET_TZ == MARKET_TZ
    end_of_day = {spec.job_id: spec.trigger for spec in build_schedule(2)}["end_of_day"]
    retry = end_of_day.get_next_fire_time(None, datetime(2025, 11, 25, 17, 0, tzinfo=MARKET_TZ))
    assert retry.time() == time(18, 30)
    assert health.END_OF_DAY_FLOOR == (datetime.combine(date(2025, 11, 25), retry.time()) + timedelta(minutes=30)).time()


def test_3c_market_zone_copies_stay_equal():
    # D50 (M3) extended to the Plan 3C copies: neutral modules keep their own ET zone instead of importing the worker.
    assert vwap._MARKET_TZ == market._MARKET_TZ == health._MARKET_TZ == MARKET_TZ
