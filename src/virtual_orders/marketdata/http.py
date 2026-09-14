"""JSON GET with bounded retries (spec 6: 3 attempts with backoff) and Decimal parsing."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

import httpx

from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, str | int],
    headers: Mapping[str, str] | None = None,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    last_error = ""
    for attempt in range(attempts):
        try:
            response = client.get(url, params=dict(params), headers=dict(headers or {}))
        except httpx.TransportError as exc:
            last_error = f"transport error: {exc}"
        else:
            if response.status_code == 200:
                try:
                    return json.loads(response.text, parse_float=Decimal)
                except ValueError as exc:
                    raise SourceDataError(f"invalid JSON from {url}") from exc
            if response.status_code not in RETRYABLE_STATUS:
                raise SourceUnavailable(f"{url} returned HTTP {response.status_code}")
            last_error = f"HTTP {response.status_code}"
        if attempt + 1 < attempts:
            sleep(backoff_seconds * 2**attempt)
    raise SourceUnavailable(f"{url} failed after {attempts} attempts: {last_error}")


def vendor_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise SourceDataError(f"{name} is not numeric: {value!r}")
    result = Decimal(value)
    if not result.is_finite():
        raise SourceDataError(f"{name} is not finite: {value!r}")
    return result
