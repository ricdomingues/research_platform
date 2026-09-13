"""Instants entering evaluator services must be timezone-aware (Plan 2 close-out entry 12)."""

from __future__ import annotations

from datetime import UTC, datetime


def require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
