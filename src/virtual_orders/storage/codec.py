"""Lossless documents for OrderState, FillConfig and prepared events.

Plan 1 close-out entries 2 (projection keeps every OrderState field) and 6 (hash computed once
from the canonical material, never from re-read jsonb).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from core.domain.hashing import canonical_json
from core.domain.models import CloseReason, EntryPath, Event, FillConfig, OrderState, OrderStatus

_DATETIME_FIELDS = frozenset({
    "entry_eligible_from", "trigger_hit_at", "stop_active_from", "opened_at", "closed_at",
    "final_event_ts", "last_bar_ts",
})
_DECIMAL_FIELDS = frozenset({
    "avg_entry", "initial_stop", "stop_current", "stop_previous", "qty_total", "qty_open",
    "realized_pnl", "costs", "dividends", "best_price", "worst_price",
})
_BOOL_FIELDS = frozenset({"zone_lost", "zone_ever_lost", "t1_done", "frozen"})
_TUPLE_FIELDS = frozenset({"frozen_reasons", "review_reasons"})
_ENUM_FIELDS: dict[str, type[StrEnum]] = {
    "status": OrderStatus, "entry_path": EntryPath, "close_reason": CloseReason,
}


def to_document(value: Any) -> Any:
    return json.loads(canonical_json(value))


def parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return parsed


def state_to_document(state: OrderState) -> dict[str, Any]:
    document: dict[str, Any] = to_document({f.name: getattr(state, f.name) for f in fields(state)})
    return document


def state_from_document(doc: Mapping[str, Any]) -> OrderState:
    expected = {f.name for f in fields(OrderState)}
    if set(doc) != expected:
        raise ValueError(
            f"state document fields mismatch: missing={sorted(expected - set(doc))} "
            f"extra={sorted(set(doc) - expected)}"
        )
    values: dict[str, Any] = {}
    for name, raw in doc.items():
        if name in _TUPLE_FIELDS:
            values[name] = tuple(raw)
        elif raw is None:
            values[name] = None
        elif name in _DATETIME_FIELDS:
            values[name] = parse_ts(raw)
        elif name in _DECIMAL_FIELDS:
            values[name] = Decimal(raw)
        elif name in _ENUM_FIELDS:
            values[name] = _ENUM_FIELDS[name](raw)
        elif name in _BOOL_FIELDS:
            if not isinstance(raw, bool):
                raise TypeError(f"{name} must be bool, got {type(raw).__name__}")
            values[name] = raw
        else:
            raise ValueError(f"unmapped OrderState field: {name}")
    return OrderState(**values)


def config_to_snapshot(config: FillConfig) -> dict[str, Any]:
    snapshot: dict[str, Any] = to_document(config.snapshot())
    return snapshot


def config_from_snapshot(doc: Mapping[str, Any]) -> FillConfig:
    return FillConfig(**dict(doc))


@dataclass(frozen=True)
class PreparedEvent:
    type: str
    event_key: str
    bar_ts: datetime | None
    price: Decimal | None
    qty: Decimal | None
    bar_batch_id: UUID | None
    payload: dict[str, Any]
    hash_material: str
    payload_hash: str

    def identity(self) -> tuple[str, str]:
        return (self.event_key, self.payload_hash)


def material_hash(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def prepare_event(event: Event) -> PreparedEvent:
    material = canonical_json(event.hash_material())
    return PreparedEvent(
        type=event.type.value,
        event_key=event.event_key,
        bar_ts=event.bar_ts,
        price=event.price,
        qty=event.qty,
        bar_batch_id=event.bar_batch_id,
        payload=json.loads(material)["payload"],
        hash_material=material,
        payload_hash=material_hash(material),
    )
