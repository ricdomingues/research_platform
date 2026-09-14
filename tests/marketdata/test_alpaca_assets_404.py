import httpx
import pytest

from virtual_orders.marketdata.alpaca import AlpacaAssets
from virtual_orders.marketdata.http import ResourceNotFound, get_json
from virtual_orders.marketdata.sources import SourceUnavailable, TickerStatus


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_404_keeps_the_parsed_json_body():
    with pytest.raises(ResourceNotFound) as caught:
        get_json(client(lambda r: httpx.Response(404, text='{"code": 40410000, "message": "not found"}')),
                 "https://x.test/a", params={}, sleep=lambda s: None)
    assert caught.value.body == {"code": 40410000, "message": "not found"}


@pytest.mark.parametrize("content", [b"", b"<html>Not Found</html>"])
def test_404_without_json_has_no_body(content):
    with pytest.raises(ResourceNotFound) as caught:
        get_json(client(lambda r: httpx.Response(404, content=content)), "https://x.test/a", params={},
                 sleep=lambda s: None)
    assert caught.value.body is None


def test_404_with_an_alpaca_error_body_is_an_unknown_asset():
    check = AlpacaAssets(
        client(lambda r: httpx.Response(404, text='{"code": 40410000, "message": "asset not found for NOPE"}')),
        "k", "s",
    )
    assert check.check_ticker("NOPE") == TickerStatus("NOPE", False, "UNKNOWN_ASSET")


@pytest.mark.parametrize("content", [b"", b"<html>Not Found</html>", b'["unexpected"]', b'{"error": 1}'])
def test_404_without_an_alpaca_error_body_is_unavailable_not_unknown(content):
    calls = []
    check = AlpacaAssets(client(lambda r: calls.append(r) or httpx.Response(404, content=content)), "k", "s",
                         base_url="https://wrong-trading-url.example")
    with pytest.raises(SourceUnavailable, match="without an Alpaca error body") as caught:
        check.check_ticker("AAPL")
    assert not isinstance(caught.value, ResourceNotFound)
    assert len(calls) == 1  # a 404 is never retried
