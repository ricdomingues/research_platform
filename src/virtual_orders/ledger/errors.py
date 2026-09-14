"""Integrity failures are never silenced (spec 3.3, 6)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

EVENT_HASH_CONFLICT = "EVENT_HASH_CONFLICT"
EVENT_BEFORE_EVALUATION_START = "EVENT_BEFORE_EVALUATION_START"
STORED_HASH_MISMATCH = "STORED_HASH_MISMATCH"
CALENDAR_MISMATCH = "CALENDAR_MISMATCH"
PROJECTION_MISSING = "PROJECTION_MISSING"
PROCESSED_BAR_MISSING = "PROCESSED_BAR_MISSING"
PROJECTION_INTEGRITY_ERROR = "PROJECTION_INTEGRITY_ERROR"
REPRODUCE_DIVERGENCE = "REPRODUCE_DIVERGENCE"


class LedgerIntegrityError(Exception):
    def __init__(
        self,
        kind: str,
        message: str,
        *,
        order_id: UUID | None = None,
        event_key: str | None = None,
        existing_hash: str | None = None,
        attempted_hash: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.order_id = order_id
        self.event_key = event_key
        self.existing_hash = existing_hash
        self.attempted_hash = attempted_hash
        self.detail: dict[str, Any] = dict(detail or {})


class ProjectionIntegrityError(LedgerIntegrityError):
    def __init__(
        self, message: str, *, order_id: UUID | None = None, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(PROJECTION_INTEGRITY_ERROR, message, order_id=order_id, detail=detail)


class HistoryDivergence(Exception):
    """Regenerated history differs from the stored authoritative history (spec 10, D2)."""

    def __init__(self, reason: str, diff: list[dict[str, Any]] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diff = diff or []


class OrderNotFound(LookupError):
    pass


class SignalNotFound(LookupError):
    pass
