"""n8n webhook adapter (spec 8 N8N_WEBHOOK_URL, D30). One bounded attempt per call; the URL never leaks."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from virtual_orders.alerts.sink import AlertDeliveryFailed

WEBHOOK_TIMEOUT_SECONDS = 5.0
IDEMPOTENCY_HEADER = "Idempotency-Key"


class N8nWebhook:
    name = "n8n"

    def __init__(self, client: httpx.Client, url: str, *, timeout_seconds: float = WEBHOOK_TIMEOUT_SECONDS) -> None:
        self._client = client
        self._url = url
        self._timeout = httpx.Timeout(timeout_seconds)

    def __repr__(self) -> str:  # the webhook URL usually embeds a secret token
        return "N8nWebhook(url=<hidden>)"

    def deliver(self, alert_key: str, document: Mapping[str, Any]) -> None:
        body = json.dumps(dict(document), ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
        try:
            response = self._client.post(
                self._url, content=body.encode("utf-8"),
                headers={"Content-Type": "application/json", IDEMPOTENCY_HEADER: alert_key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise AlertDeliveryFailed(status_code=None, error_type=type(exc).__name__) from None
        if not 200 <= response.status_code < 300:
            raise AlertDeliveryFailed(status_code=response.status_code, error_type="HTTPStatus")
