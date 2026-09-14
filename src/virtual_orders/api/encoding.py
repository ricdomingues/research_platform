"""JSON in with Decimal (never float); JSON out with Decimal as normalized string and UTC ISO datetimes."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import Request, Response

from core.domain.hashing import CANONICAL_CONTEXT
from virtual_orders.api.errors import ApiError

JSON_MEDIA_TYPE = "application/json"


def to_json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, str | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return "0" if value == 0 else format(value.normalize(CANONICAL_CONTEXT), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime in API response")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_json_value(item) for item in value]
    raise TypeError(f"unsupported type in API response: {type(value).__name__}")


def json_response(content: Any, status_code: int = 200) -> Response:
    body = json.dumps(to_json_value(content), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return Response(body, status_code=status_code, media_type=JSON_MEDIA_TYPE)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not allowed")


async def read_json_object(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        body = json.loads(raw, parse_float=Decimal, parse_constant=_reject_constant)
    except ValueError as exc:
        # I3: never the decoder's message (it can quote raw request bytes); position fields only.
        detail: dict[str, Any] = {}
        if isinstance(exc, json.JSONDecodeError):
            detail = {"line": exc.lineno, "column": exc.colno}
        raise ApiError(422, "INVALID_JSON", detail=detail) from exc
    if not isinstance(body, dict):
        raise ApiError(422, "INVALID_JSON", detail={"code": "NOT_AN_OBJECT"})
    return body
