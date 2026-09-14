from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from core.domain.models import Origin
from tests.integration.api.conftest import post_json
from tests.integration.support import (
    CODE_VERSION,
    DAY,
    PRICE_SOURCE,
    TICKER,
    backdated_batch,
    count,
    raw,
    scenario_bars,
    signal_body,
)
from tests.support import et
from virtual_orders.evaluator.cycle import run_live_cycle
from virtual_orders.evaluator.replay import reproduce_orders
from virtual_orders.ledger.orders import get_order
from virtual_orders.ledger.runs import get_run, list_segments


def new_signal(api, **overrides):
    return post_json(api.client, "/signals", signal_body(**overrides)).json()


def closed_order(api) -> UUID:
    order_id = UUID(new_signal(api)["auto_order_id"])
    api.bars.load(scenario_bars())
    for hm in ("10:30", "11:30", "13:00"):
        run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, hm))
    return order_id


def test_manual_order_is_created_when_actionable(api):
    signal_id = new_signal(api, auto_order=False)["signal_id"]
    api.bars.load(scenario_bars())
    api.clock.set(et(DAY, "09:50"))

    response = api.client.post(f"/signals/{signal_id}/orders")

    assert response.status_code == 201
    body = response.json()
    assert body["partial_bar_skipped"] is False and body["actionability_run_id"]
    with api.services.engine.connect() as conn:
        order = get_order(conn, UUID(body["order_id"]))
    assert order.origin is Origin.MANUAL_USER and order.price_source == PRICE_SOURCE


def test_manual_order_after_the_fill_is_422_with_reason(api):
    signal_id = new_signal(api, auto_order=False)["signal_id"]
    api.bars.load(scenario_bars())
    api.clock.set(et(DAY, "10:30"))
    response = api.client.post(f"/signals/{signal_id}/orders")
    assert response.status_code == 422
    error = response.json()["error"]
    assert (error["code"], error["reason"]) == ("SIGNAL_NO_LONGER_ACTIONABLE", "ENTRY_OPPORTUNITY_ALREADY_OCCURRED")


def test_manual_order_with_the_provider_down_is_503_and_creates_nothing(api):
    signal_id = new_signal(api, auto_order=False)["signal_id"]
    api.bars.failing.add(TICKER)
    api.clock.set(et(DAY, "09:50"))
    response = api.client.post(f"/signals/{signal_id}/orders")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ACTIONABILITY_UNVERIFIABLE"
    assert "(fake)" not in response.text
    assert count(api.services.engine, "orders") == 0


def test_manual_order_after_validity_is_422_expired(api):
    signal_id = new_signal(api, auto_order=False)["signal_id"]
    api.clock.set(et("2025-12-05", "10:00"))
    response = api.client.post(f"/signals/{signal_id}/orders")
    assert response.status_code == 422 and response.json()["error"]["code"] == "SIGNAL_EXPIRED"


def test_manual_order_for_unknown_or_malformed_signal(api):
    unknown = api.client.post(f"/signals/{uuid4()}/orders")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "SIGNAL_NOT_FOUND"
    malformed = api.client.post("/signals/not-a-uuid/orders")
    assert malformed.status_code == 422 and malformed.json()["error"]["code"] == "REQUEST_INVALID"


def test_cancel_then_cancel_again_is_409(api):
    order_id = new_signal(api)["auto_order_id"]
    api.clock.set(et(DAY, "09:45"))
    first = api.client.post(f"/orders/{order_id}/cancel")
    assert first.status_code == 200
    assert first.json() == {"order_id": order_id, "event_keys": ["CANCELED"]}
    second = api.client.post(f"/orders/{order_id}/cancel")
    assert second.status_code == 409 and second.json()["error"]["code"] == "ORDER_ALREADY_FINAL"


def test_cancel_unknown_order_is_404(api):
    response = api.client.post(f"/orders/{uuid4()}/cancel")
    assert response.status_code == 404 and response.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_cancel_replay_order_is_409_read_only(api):
    order_id = closed_order(api)
    replay_id = reproduce_orders(api.services.engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    response = api.client.post(f"/orders/{replay_id}/cancel")
    assert response.status_code == 409 and response.json()["error"]["code"] == "REPLAY_ORDER_READ_ONLY"


def test_reproduce_by_ids(api):
    order_id = closed_order(api)
    response = post_json(api.client, "/replay", {"mode": "REPRODUCE", "order_ids": [str(order_id)]})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "REPRODUCE" and list(body["created"]) == [str(order_id)] and body["failures"] == {}


def test_reproduce_divergence_is_409_with_the_diff_per_order(api):
    """Same divergence setup as test_reproduce.test_divergent_history_fails_records_incident_and_freezes_source."""
    diverging = UUID(new_signal(api)["auto_order_id"])
    identical = UUID(new_signal(api, client_signal_id="msft", ticker="MSFT")["auto_order_id"])
    api.bars.load(scenario_bars())
    api.bars.load(scenario_bars(ticker="MSFT"))
    run_live_cycle(api.services.engine, api.services.gateway, code_version=CODE_VERSION, market_now=et(DAY, "10:30"))
    with api.services.engine.connect() as conn:
        run = get_run(conn, list_segments(conn, diverging)[0].run_id)
    backdated_batch(api.services.engine, TICKER, [raw(DAY, "10:05", 104, 104, 104, 104)], run.data_as_of)

    response = post_json(api.client, "/replay", {"mode": "REPRODUCE", "order_ids": [str(diverging), str(identical)]})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "REPRODUCE_DIVERGED" and error["reason"] is None
    detail = error["detail"]
    assert detail["mode"] == "REPRODUCE" and detail["run_id"]
    assert list(detail["identical"]) == [str(identical)] and detail["failures"] == {}
    assert list(detail["diverged"]) == [str(diverging)]
    entry = detail["diverged"][str(diverging)]
    assert entry["incident_id"] is not None and entry["reason"] and isinstance(entry["diff"], list)


def test_recalculate_with_overrides(api):
    order_id = closed_order(api)
    response = post_json(api.client, "/replay", {
        "mode": "RECALCULATE", "order_ids": [str(order_id)],
        "config_overrides": {"commission_per_execution": Decimal("1")},
    })
    assert response.status_code == 200
    replay_id = UUID(response.json()["created"][str(order_id)])
    with api.services.engine.connect() as conn:
        assert get_order(conn, replay_id).config.commission_per_execution == Decimal("1")


@pytest.mark.parametrize(("body", "errors"), [
    ({"mode": "SOMETIMES"}, ["INVALID_MODE"]),
    ({"mode": "REPRODUCE", "order_ids": []}, ["INVALID_ORDER_IDS"]),
    ({"mode": "REPRODUCE", "order_ids": ["x"]}, ["INVALID_ORDER_IDS"]),
    ({"mode": "REPRODUCE", "from": "2025-11-25T00:00:00", "to": "2025-11-26T00:00:00Z"}, ["NAIVE_DATETIME:from"]),
    ({"mode": "REPRODUCE", "from": "yesterday", "to": "2025-11-26T00:00:00Z"}, ["INVALID_DATETIME:from"]),
    ({"mode": "REPRODUCE", "order_ids": [str(uuid4())], "config_overrides": {"risk_amount": 5}},
     ["NOT_ALLOWED_FOR_REPRODUCE:config_overrides"]),
    ({"mode": "RECALCULATE", "order_ids": [str(uuid4())], "config_overrides": [1]}, ["INVALID_OBJECT:config_overrides"]),
    ({"mode": "RECALCULATE", "order_ids": [str(uuid4())], "extra": 1}, ["UNKNOWN_FIELD:extra"]),
])
def test_malformed_replay_requests_are_422_before_any_run(api, body, errors):
    response = post_json(api.client, "/replay", body)
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "REPLAY_REQUEST_INVALID", "reason": None, "detail": {"errors": errors}}
    assert count(api.services.engine, "evaluation_runs") == 0


def test_service_level_replay_rejections_are_422(api):
    order_id = closed_order(api)
    replay_id = reproduce_orders(api.services.engine, code_version=CODE_VERSION, order_ids=[order_id]).created[order_id]
    cases = [
        {"mode": "REPRODUCE"},
        {"mode": "REPRODUCE", "order_ids": [str(replay_id)]},
        {"mode": "RECALCULATE", "order_ids": [str(order_id)], "config_overrides": {"dividend_tolerance": Decimal("0.1")}},
        {"mode": "RECALCULATE", "order_ids": [str(order_id)], "data_as_of": "2100-01-01T00:00:00Z"},
    ]
    for body in cases:
        response = post_json(api.client, "/replay", body)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "REPLAY_REQUEST_INVALID"
        assert response.json()["error"]["detail"]["errors"]
