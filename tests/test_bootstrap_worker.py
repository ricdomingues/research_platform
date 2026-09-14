from dataclasses import replace

import httpx
import pytest
from sqlalchemy.exc import ArgumentError

from tests.config_support import BASE_ENV
from virtual_orders import bootstrap
from virtual_orders.bootstrap import build_services
from virtual_orders.config import load_settings
from virtual_orders.notify.n8n import N8nWebhook


def settings(**overrides):
    return replace(load_settings(BASE_ENV), **overrides)


def offline_client(calls):
    return httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)))


def test_owned_client_is_closed_when_the_engine_cannot_be_built(monkeypatch):
    created: list[httpx.Client] = []
    real_client = httpx.Client

    def recording_client(*args, **kwargs):
        instance = real_client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        created.append(instance)
        return instance

    def broken_engine(url):
        raise ArgumentError("could not parse the URL (test)")

    monkeypatch.setattr(bootstrap.httpx, "Client", recording_client)
    monkeypatch.setattr(bootstrap, "make_engine", broken_engine)

    with pytest.raises(ArgumentError):
        build_services(settings())

    assert len(created) == 1 and created[0].is_closed


def test_injected_client_is_left_open_when_the_engine_cannot_be_built(monkeypatch):
    client = offline_client([])

    def broken_engine(url):
        raise ArgumentError("could not parse the URL (test)")

    monkeypatch.setattr(bootstrap, "make_engine", broken_engine)
    with pytest.raises(ArgumentError):
        build_services(settings(), http_client=client)
    assert not client.is_closed
    client.close()


def test_webhook_is_disabled_without_a_url_and_composed_with_one():
    calls: list[httpx.Request] = []
    disabled = build_services(settings(), http_client=offline_client(calls))
    try:
        assert disabled.alert_sink is None
    finally:
        disabled.close()

    enabled = build_services(settings(n8n_webhook_url="https://n8n.test/hook/secret-token"),
                             http_client=offline_client(calls))
    try:
        assert isinstance(enabled.alert_sink, N8nWebhook)
        assert "secret-token" not in repr(enabled)
        assert "secret-token" not in repr(enabled.alert_sink)
    finally:
        enabled.close()
    assert calls == []
