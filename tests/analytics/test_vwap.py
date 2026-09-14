from decimal import Decimal

import pytest

from core.domain.models import Bar
from tests.support import et
from virtual_orders.analytics.vwap import METHOD, session_vwap


def candle(day, hm, o, h, l, c, v):  # noqa: E741
    return Bar(ts=et(day, hm), open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=Decimal(v))


def test_vwap_is_cumulative_per_session_and_restarts_each_et_date():
    bars = [
        candle("2025-11-26", "09:30", "20", "21", "19", "20", "50"),
        candle("2025-11-25", "09:32", "12", "12", "12", "12", "0"),
        candle("2025-11-25", "09:30", "10", "11", "9", "10", "100"),
        candle("2025-11-25", "09:31", "12", "13", "11", "12", "300"),
    ]
    points = session_vwap(bars)
    assert [(p.ts, str(p.value)) for p in points] == [
        (et("2025-11-25", "09:30"), "10.0000"),
        (et("2025-11-25", "09:31"), "11.5000"),  # (10*100 + 12*300) / 400
        (et("2025-11-25", "09:32"), "11.5000"),  # zero volume keeps the running value
        (et("2025-11-26", "09:30"), "20.0000"),  # new ET date: restarted
    ]
    assert METHOD == "SESSION_VWAP_TYPICAL_PRICE_V1"


def test_without_volume_yet_the_vwap_is_the_close():
    (point,) = session_vwap([candle("2025-11-25", "09:30", "5", "6", "4", "5.5", "0")])
    assert str(point.value) == "5.5000"


def test_duplicate_minutes_are_rejected():
    bar = candle("2025-11-25", "09:30", "10", "11", "9", "10", "100")
    with pytest.raises(ValueError, match="duplicate bar minute"):
        session_vwap([bar, bar])
