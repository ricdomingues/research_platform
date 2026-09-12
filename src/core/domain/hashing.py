"""Canonical serialization used for payload hashes and idempotency (spec 3.3)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal
from enum import Enum
from typing import Any
from uuid import UUID

# Explicit context so hashes never depend on the caller's ambient decimal context.
CANONICAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise TypeError("floats are not allowed in canonical payloads; use Decimal")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"non-finite Decimal: {value}")
        if value == 0:
            return "0"
        return format(value.normalize(CANONICAL_CONTEXT), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError(f"canonical payload dict keys must be str, got {type(key).__name__}")
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise TypeError(f"unsupported type in canonical payload: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
