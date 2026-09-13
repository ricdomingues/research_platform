"""Per-order result of an evaluator operation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class OrderOutcome:
    order_id: UUID
    event_keys: tuple[str, ...] = ()
    segment: tuple[datetime, datetime] | None = None
    error: str | None = None
