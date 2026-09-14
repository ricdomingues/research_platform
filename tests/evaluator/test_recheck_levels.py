"""T10 (Plan 3B entry 16): the recheck's level-touch check uses the stop in force at the session close, rebuilt from
the ledger, never the projection's current stop. In fill_model v1 both agree at integration level (one stop move, and
the projection keeps stop_previous/stop_active_from), so the difference is pinned with a synthetic history (D55)."""

from decimal import Decimal

from core.dataquality import ReviewFlag, missing_bar_reviews
from core.domain.models import OrderState, OrderStatus
from tests.support import bar, et, long_signal
from virtual_orders.evaluator.recheck import levels_state_at_close
from virtual_orders.storage.codec import PreparedEvent

DAY, NEXT = "2025-11-25", "2025-11-26"
SIGNAL = long_signal()  # zone 100-102, stop 97, targets 106 / 110
MINUTE = et(DAY, "10:20")
TOUCHES_ONLY_101 = {MINUTE: bar(MINUTE, "101", "101.5", "100.5", "101")}  # zone edges, stop 97 and targets untouched
# A projection whose stop moved to 101 before the missing minute, while the ledger history below says otherwise.
PROJECTION = OrderState(
    status=OrderStatus.OPEN, avg_entry=Decimal("101"), initial_stop=Decimal("97"), stop_current=Decimal("101"),
    stop_previous=Decimal("97"), stop_active_from=et(DAY, "10:00"), qty_total=Decimal("25"), qty_open=Decimal("13"),
)


def target1_hit(bar_ts, active_from):
    return PreparedEvent(
        type="TARGET1_HIT", event_key="TARGET1_HIT", bar_ts=bar_ts, price=Decimal("106"), qty=Decimal("12"),
        bar_batch_id=None, payload={"new_stop_level": "101", "stop_active_from": active_from.isoformat()},
        hash_material="synthetic", payload_hash="synthetic",
    )


def test_the_projection_alone_would_flag_a_touch_of_a_stop_not_in_force_that_session():
    assert missing_bar_reviews([MINUTE], TOUCHES_ONLY_101, SIGNAL, PROJECTION) == [
        ReviewFlag("MISSING_BAR_LEVEL_TOUCH", MINUTE.isoformat())]


def test_levels_at_close_come_from_the_ledger_and_do_not_flag_that_touch():
    history = [target1_hit(et(NEXT, "11:00"), et(NEXT, "11:01"))]  # the stop only moved on the next session

    derived = levels_state_at_close(PROJECTION, SIGNAL.stop, history, "v1", et(DAY, "16:00"))

    assert derived is not None
    assert (derived.stop_current, derived.stop_previous, derived.stop_active_from) == (Decimal("97"), None, None)
    assert missing_bar_reviews([MINUTE], TOUCHES_ONLY_101, SIGNAL, derived) == []


def test_a_stop_move_before_the_minute_is_honoured_by_the_derivation():
    history = [target1_hit(et(DAY, "10:10"), et(DAY, "10:11"))]

    derived = levels_state_at_close(PROJECTION, SIGNAL.stop, history, "v1", et(DAY, "16:00"))

    assert derived is not None
    assert (derived.stop_current, derived.stop_previous, derived.stop_active_from) == (
        Decimal("101"), Decimal("97"), et(DAY, "10:11"))
    assert missing_bar_reviews([MINUTE], TOUCHES_ONLY_101, SIGNAL, derived) == [
        ReviewFlag("MISSING_BAR_LEVEL_TOUCH", MINUTE.isoformat())]
