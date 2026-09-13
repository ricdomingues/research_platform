from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.integration.api.conftest import API_KEY, post_json
from tests.integration.support import count, signal_body, submit_default
from virtual_orders.api.app import create_app
from virtual_orders.storage import tables


def test_every_route_rejects_a_missing_or_wrong_key(api):
    # fastapi>=0.141 wraps `include_router`ed routes in `_IncludedRouter` on `app.routes`; the public
    # `iter_route_contexts` flattens them back to the underlying `APIRoute` objects (brief predates this).
    anonymous = TestClient(api.client.app)
    routes = [ctx.route for ctx in iter_route_contexts(api.client.app.routes) if isinstance(ctx.route, APIRoute)]
    assert routes
    for route in routes:
        path = route.path.replace("{signal_id}", str(uuid4())).replace("{order_id}", str(uuid4()))
        for method in route.methods:
            for headers in ({}, {"X-API-Key": "wrong"}):
                response = anonymous.request(method, path, headers=headers)
                assert response.status_code == 401, (method, path)
                assert response.json() == {"error": {"code": "UNAUTHORIZED", "reason": None, "detail": {}}}


def test_docs_unknown_routes_and_wrong_methods_use_the_error_envelope(api):
    for path in ("/docs", "/redoc", "/openapi.json", "/no-such-route"):
        response = api.client.get(path)
        assert response.status_code == 404, path
        assert response.json() == {"error": {"code": "NOT_FOUND", "reason": None, "detail": {}}}
    wrong = api.client.put("/signals")
    assert wrong.status_code == 405
    assert wrong.json() == {"error": {"code": "METHOD_NOT_ALLOWED", "reason": None, "detail": {}}}


def test_unhandled_errors_are_500_without_the_message(api):
    class ExplodingCheck:
        name = "exploding"

        def check_ticker(self, ticker):
            raise RuntimeError("internal-detail-that-must-not-leak")

    services = replace(api.services, ticker_check=ExplodingCheck())
    with TestClient(create_app(services), headers={"X-API-Key": API_KEY}, raise_server_exceptions=False) as client:
        response = post_json(client, "/signals", signal_body())
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR", "reason": None, "detail": {"type": "RuntimeError"}}}
    assert "internal-detail-that-must-not-leak" not in response.text


def test_signal_is_created_then_returned_as_existing_without_a_second_provider_call(api):
    first = post_json(api.client, "/signals", signal_body())
    assert first.status_code == 201
    created = first.json()
    assert created["status"] == "CREATED" and created["auto_order_id"] is not None

    again = post_json(api.client, "/signals", signal_body())
    assert again.status_code == 200
    assert again.json() == {**created, "status": "EXISTING"}
    assert api.tickers.calls == ["AAPL"]


def test_conflicting_payload_is_409(api):
    post_json(api.client, "/signals", signal_body())
    response = post_json(api.client, "/signals", signal_body(stop=Decimal("96")))
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "IDEMPOTENCY_CONFLICT"
    assert error["detail"]["client_signal_id"] == "rex-2025-11-25-aapl"


def test_invalid_signal_is_422_and_never_calls_the_provider(api):
    response = post_json(api.client, "/signals", signal_body(stop=Decimal("101")))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SIGNAL_VALIDATION_FAILED"
    assert response.json()["error"]["detail"]["errors"]
    assert api.tickers.calls == []
    assert count(api.services.engine, "signals") == 0


def test_untradable_ticker_is_422_and_nothing_is_stored(api):
    api.tickers.untradable["ZZZZ"] = "INACTIVE"
    response = post_json(api.client, "/signals", signal_body(ticker="ZZZZ"))
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "SIGNAL_VALIDATION_FAILED", "reason": None, "detail": {"errors": ["TICKER_NOT_TRADABLE:INACTIVE"]},
    }
    assert count(api.services.engine, "signals") == 0


def test_ticker_check_unavailable_is_503_and_nothing_is_stored(api):
    api.tickers.failing = True
    response = post_json(api.client, "/signals", signal_body())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TICKER_UNVERIFIABLE"
    assert count(api.services.engine, "signals") == 0


def test_body_numbers_are_read_as_decimal_never_float(api):
    response = post_json(api.client, "/signals", signal_body(entry_zone_low=Decimal("100.10000000000000001")))
    assert response.status_code == 201
    with api.services.engine.connect() as conn:
        stored = conn.execute(select(tables.signals.c.entry_zone_low)).scalar_one()
    assert stored == Decimal("100.10000000000000001")


def test_integer_and_decimal_spellings_of_a_price_are_the_same_submission(api):
    first = post_json(api.client, "/signals", signal_body(entry_zone_low=Decimal("100")))  # JSON token 100
    assert first.status_code == 201
    retry = post_json(api.client, "/signals", signal_body(entry_zone_low=Decimal("100.0"), stop=Decimal("97.00")))
    assert retry.status_code == 200 and retry.json()["status"] == "EXISTING"

    direct = submit_default(api.services.engine, client_signal_id="direct")  # service call with Decimal values
    via_api = post_json(api.client, "/signals", signal_body(client_signal_id="direct"))
    assert via_api.status_code == 200 and via_api.json()["signal_id"] == str(direct.signal_id)


def test_malformed_json_is_422(api):
    for raw in ("not json", "[1, 2]", '{"stop": NaN}'):
        response = api.client.post("/signals", content=raw, headers={"Content-Type": "application/json"})
        assert response.status_code == 422, raw
        assert response.json()["error"]["code"] == "INVALID_JSON"


def test_list_signals_filters_by_market_date_and_strategy(api):
    post_json(api.client, "/signals", signal_body())
    post_json(api.client, "/signals", signal_body(client_signal_id="other", strategy="OTHER", ticker="MSFT"))

    listed = api.client.get("/signals", params={"date": "2025-11-25", "strategy": "REXSHARE"})
    assert listed.status_code == 200
    signals = listed.json()["signals"]
    assert [s["client_signal_id"] for s in signals] == ["rex-2025-11-25-aapl"]
    assert signals[0]["entry_zone_low"] == "100" and signals[0]["auto_order_status"] == "PENDING"
    assert len(api.client.get("/signals").json()["signals"]) == 2
    assert api.client.get("/signals", params={"date": "2025-11-24"}).json() == {"signals": []}
    assert api.client.get("/signals", params={"limit": 0}).json()["error"]["code"] == "REQUEST_INVALID"
