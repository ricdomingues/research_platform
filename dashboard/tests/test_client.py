import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from dashboard.client import ApiClient, ApiRequestFailed, ApiUnreachable, DashboardConfigError


def client_for(handler):
    return ApiClient("http://api.test", "secret-key", transport=httpx.MockTransport(handler))


def test_requests_carry_the_key_and_numbers_come_back_as_decimal():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text='{"orders": [{"r_multiple": "1.75", "win_rate": 0.5}]}')

    orders = client_for(handler).orders(status="CLOSED", needs_review=False)
    assert orders == [{"r_multiple": "1.75", "win_rate": Decimal("0.5")}]
    request = seen[0]
    assert request.headers["X-API-Key"] == "secret-key"
    assert request.url.path == "/orders"
    assert dict(request.url.params) == {"status": "CLOSED", "replay": "false", "needs_review": "false", "limit": "200"}


def test_query_values_are_serialized_and_none_is_dropped():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"signals": [], "groups": []})

    api = client_for(handler)
    api.metrics(include_needs_review=True, start=datetime(2025, 11, 25, 14, 30, tzinfo=UTC))
    api.signals(day=date(2025, 11, 25))
    assert dict(seen[0].url.params) == {"include_needs_review": "true", "replay": "false",
                                        "from": "2025-11-25T14:30:00+00:00"}
    assert dict(seen[1].url.params) == {"date": "2025-11-25", "limit": "200"}
    with pytest.raises(ValueError, match="naive datetime"):
        api.metrics(start=datetime(2025, 11, 25, 14, 30))  # noqa: DTZ001


def test_bodies_send_decimals_as_exact_json_numbers_and_path_segments_are_quoted():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json={"rule": {"id": "r-1"}, "ticker": "BRK/B", "status": "CREATED"})

    api = client_for(handler)
    rule = api.create_rule("MSFT", {"kind": "PRICE_CROSS", "level": Decimal("101.50"), "direction": "ABOVE"})
    assert rule == {"id": "r-1"}
    assert b"101.50" in seen[0].content
    assert json.loads(seen[0].content, parse_float=Decimal)["level"] == Decimal("101.50")
    assert seen[0].headers["Content-Type"] == "application/json"
    api.add_ticker("BRK/B")
    assert seen[1].method == "PUT" and seen[1].url.raw_path == b"/watchlist/BRK%2FB"


def test_error_envelopes_become_api_request_failed_with_fixed_fields():
    def handler(request):
        return httpx.Response(422, json={"error": {"code": "SIGNAL_NO_LONGER_ACTIONABLE",
                                                   "reason": "ENTRY_OPPORTUNITY_ALREADY_OCCURRED", "detail": {}}})

    with pytest.raises(ApiRequestFailed) as caught:
        client_for(handler).create_manual_order("s-1")
    error = caught.value
    assert (error.status_code, error.code, error.reason, error.detail) == (
        422, "SIGNAL_NO_LONGER_ACTIONABLE", "ENTRY_OPPORTUNITY_ALREADY_OCCURRED", {})


def test_non_envelope_errors_and_invalid_bodies_use_fixed_codes():
    with pytest.raises(ApiRequestFailed) as bad_gateway:
        client_for(lambda request: httpx.Response(502, text="<html>proxy secret</html>")).watchlist()
    assert (bad_gateway.value.code, str(bad_gateway.value)) == ("HTTP_502", "HTTP_502")
    with pytest.raises(ApiRequestFailed) as not_json:
        client_for(lambda request: httpx.Response(200, text="not json")).watchlist()
    assert not_json.value.code == "INVALID_RESPONSE"


def test_transport_failures_keep_only_the_exception_type():
    def handler(request):
        raise httpx.ConnectError("connection refused to http://api.test with secret-key")

    with pytest.raises(ApiUnreachable) as caught:
        client_for(handler).virtual_portfolio()
    assert (caught.value.error_type, str(caught.value)) == ("ConnectError", "ConnectError")


def test_non_finite_numbers_in_responses_are_rejected_with_the_fixed_code():
    with pytest.raises(ApiRequestFailed) as caught:
        client_for(lambda request: httpx.Response(200, text='{"value": NaN}')).watchlist()
    assert caught.value.code == "INVALID_RESPONSE"


def test_non_finite_numbers_in_request_bodies_are_rejected_before_sending():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json={"rule": {"id": "r-1"}})

    api = client_for(handler)
    with pytest.raises(ApiRequestFailed) as caught:
        api.create_rule("MSFT", {"kind": "PRICE_CROSS", "level": float("nan"), "direction": "ABOVE"})
    assert caught.value.code == "INVALID_REQUEST"
    assert seen == []


def test_health_returns_the_unhealthy_body_instead_of_raising():
    body = {"state": "UNHEALTHY", "causes": [{"code": "DATABASE_UNAVAILABLE"}], "facts": None}
    assert client_for(lambda request: httpx.Response(503, json=body)).health() == body


def test_configuration_comes_from_the_environment_and_never_shows_the_key():
    with pytest.raises(DashboardConfigError) as caught:
        ApiClient.from_environment({"API_KEY": "  "})
    assert caught.value.missing == ["MISSING:DASHBOARD_API_URL", "MISSING:API_KEY"]
    api = ApiClient.from_environment({"DASHBOARD_API_URL": "http://api:8000", "API_KEY": "secret-key"})
    assert "secret-key" not in repr(api) and "api:8000" in repr(api)
    api.close()


def test_observation_routes_send_iso_dates():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"session_day": "2025-11-25"})

    api = client_for(handler)
    assert api.observation_report(date(2025, 11, 25)) == {"session_day": "2025-11-25"}
    api.observation_summary(date(2025, 11, 12), date(2025, 11, 25))
    assert (seen[0].url.path, dict(seen[0].url.params)) == ("/observation/report", {"day": "2025-11-25"})
    assert (seen[1].url.path, dict(seen[1].url.params)) == (
        "/observation/summary", {"from": "2025-11-12", "to": "2025-11-25"})
    assert seen[0].headers["X-API-Key"] == "secret-key"
