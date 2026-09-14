from datetime import timedelta
from decimal import Decimal

import pytest

from tests.support import bar, et
from virtual_orders.alerts.rules import pressure_alert, price_crossings
from virtual_orders.alerts.watchlist import AlertRuleInvalid, CrossDirection, RuleDraft, RuleKind, parse_rule_body
from virtual_orders.analytics.pressure import PressureSide

DAY = "2025-11-25"


def closes(*values):
    return [bar(et(DAY, f"10:{i:02d}"), v, v, v, v) for i, v in enumerate(values)]


def test_parse_price_cross_accepts_json_integers_and_defaults_the_cooldown():
    assert parse_rule_body({"kind": "PRICE_CROSS", "level": 101, "direction": "ABOVE"}) == RuleDraft(
        RuleKind.PRICE_CROSS, Decimal("101"), CrossDirection.ABOVE, None, None, 30)


def test_parse_pressure_rule():
    body = {"kind": "PRESSURE", "cmf_threshold": Decimal("0.3"), "window_bars": 20, "cooldown_minutes": 0}
    assert parse_rule_body(body) == RuleDraft(RuleKind.PRESSURE, None, None, Decimal("0.3"), 20, 0)


@pytest.mark.parametrize("body, errors", [
    ({"kind": "PRICE_CROSS", "level": -1, "direction": "UP", "window_bars": 10, "extra": 1},
     ["UNKNOWN_FIELD:extra", "INVALID_CHOICE:direction", "OUT_OF_RANGE:level", "NOT_ALLOWED:window_bars"]),
    ({"kind": "PRESSURE"}, ["MISSING_FIELD:cmf_threshold", "MISSING_FIELD:window_bars"]),
    ({"kind": "PRESSURE", "cmf_threshold": Decimal("1"), "window_bars": 4, "level": 5},
     ["OUT_OF_RANGE:cmf_threshold", "OUT_OF_RANGE:window_bars", "NOT_ALLOWED:level"]),
    ({"kind": "VOLUME"}, ["INVALID_CHOICE:kind"]),
    ({"kind": "PRICE_CROSS", "level": True, "direction": "ABOVE"}, ["INVALID_DECIMAL:level"]),
    ({"kind": "PRICE_CROSS", "level": 1.5, "direction": "ABOVE"}, ["INVALID_DECIMAL:level"]),
    ({"kind": "PRICE_CROSS", "direction": "BELOW", "cooldown_minutes": 2000},
     ["OUT_OF_RANGE:cooldown_minutes", "MISSING_FIELD:level"]),
])
def test_parse_rejects_invalid_rules_with_every_error(body, errors):
    with pytest.raises(AlertRuleInvalid) as caught:
        parse_rule_body(body)
    assert caught.value.errors == errors


def test_price_crossings_use_closes_only_and_the_first_bar_never_crosses():
    bars = closes(101.5, 100, 101.2, 100.5, 102)
    assert [b.ts for b in price_crossings(bars, level=Decimal("101"), direction="ABOVE",
                                          cooldown=timedelta(0))] == [et(DAY, "10:02"), et(DAY, "10:04")]
    assert [b.ts for b in price_crossings(bars, level=Decimal("101"), direction="BELOW",
                                          cooldown=timedelta(0))] == [et(DAY, "10:01"), et(DAY, "10:03")]


def test_intrabar_touch_is_not_a_crossing():
    touched = [bar(et(DAY, "10:00"), 100, 100, 100, 100), bar(et(DAY, "10:01"), 100, 102, 99, 100.5)]
    assert price_crossings(touched, level=Decimal("101"), direction="ABOVE", cooldown=timedelta(0)) == []


def test_cooldown_suppresses_repeated_crossings_including_across_calls():
    bars = closes(100, 101.5, 100, 101.5)
    assert [b.ts for b in price_crossings(bars, level=Decimal("101"), direction="ABOVE",
                                          cooldown=timedelta(minutes=5))] == [et(DAY, "10:01")]
    assert price_crossings(bars, level=Decimal("101"), direction="ABOVE", cooldown=timedelta(minutes=5),
                           last_alert_ts=et(DAY, "10:00")) == []


def rising(count):
    return [bar(et(DAY, f"09:{30 + i}"), 100 + i, 100.5 + i, 99.8 + i, 100.5 + i) for i in range(count)]


def test_pressure_alert_needs_a_full_window_and_respects_the_cooldown():
    assert pressure_alert(rising(4), cmf_threshold=Decimal("0.5"), window_bars=5, cooldown=timedelta(0)) is None
    found = pressure_alert(rising(7), cmf_threshold=Decimal("0.5"), window_bars=5, cooldown=timedelta(0))
    assert found is not None
    estimate, side = found
    assert side is PressureSide.BUY and estimate.bars == 5 and estimate.first_bar_ts == et(DAY, "09:32")
    assert pressure_alert(rising(7), cmf_threshold=Decimal("0.5"), window_bars=5, cooldown=timedelta(minutes=30),
                          last_alert_ts=et(DAY, "09:20")) is None
