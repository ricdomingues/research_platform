"""Single API key via `X-API-Key` (spec 5.1), compared in constant time."""

from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Request

from virtual_orders.api.errors import ApiError

API_KEY_HEADER = "X-API-Key"


def require_api_key(expected: str) -> Callable[[Request], None]:
    expected_bytes = expected.encode("utf-8")

    def dependency(request: Request) -> None:
        provided = request.headers.get(API_KEY_HEADER)
        if provided is None or not hmac.compare_digest(provided.encode("utf-8"), expected_bytes):
            raise ApiError(401, "UNAUTHORIZED")

    return dependency
