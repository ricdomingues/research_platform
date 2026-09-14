import json

import httpx
import pytest

from virtual_orders.alerts.sink import AlertDeliveryFailed
from virtual_orders.notify.n8n import IDEMPOTENCY_HEADER, N8nWebhook

URL = "https://n8n.test/webhook/secret-token"


def webhook(handler) -> N8nWebhook:
    return N8nWebhook(httpx.Client(transport=httpx.MockTransport(handler)), URL)


def test_posts_one_json_document_with_the_idempotency_key():
    seen: list[httpx.Request] = []
    sink = webhook(lambda request: seen.append(request) or httpx.Response(200))

    sink.deliver("ORDER_EVENT:o1:FILLED", {"kind": "ORDER_EVENT", "price": "101.5", "b": 1})

    (request,) = seen
    assert request.method == "POST" and str(request.url) == URL
    assert request.headers[IDEMPOTENCY_HEADER] == "ORDER_EVENT:o1:FILLED"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {"kind": "ORDER_EVENT", "price": "101.5", "b": 1}
    assert sink.name == "n8n"


def test_non_2xx_raises_with_the_status_and_no_retry():
    calls: list[httpx.Request] = []
    sink = webhook(lambda request: calls.append(request) or httpx.Response(502, text="bad gateway secret-token"))
    with pytest.raises(AlertDeliveryFailed) as caught:
        sink.deliver("k", {})
    assert caught.value.status_code == 502 and caught.value.error_type == "HTTPStatus"
    assert len(calls) == 1
    assert "secret-token" not in str(caught.value)


def test_timeout_raises_the_exception_type_only():
    def handler(request):
        raise httpx.ReadTimeout("timed out talking to secret-token", request=request)

    with pytest.raises(AlertDeliveryFailed) as caught:
        webhook(handler).deliver("k", {})
    assert caught.value.status_code is None and caught.value.error_type == "ReadTimeout"
    assert "secret-token" not in str(caught.value) and caught.value.__cause__ is None


def test_repr_hides_the_url():
    assert "secret-token" not in repr(webhook(lambda request: httpx.Response(200)))
