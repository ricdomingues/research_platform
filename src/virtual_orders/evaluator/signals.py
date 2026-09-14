"""Signal intake with strict idempotency (spec 3.3) and automatic AUTO_STRATEGY order."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine

from core.domain.calendar import evaluation_start_ts, signal_valid_until_ts
from core.domain.hashing import sha256_hex
from core.domain.models import Direction, FillConfig, Origin, SignalSpec, coerce_decimal
from core.domain.validation import validate_signal
from core.fills import get_fill_model
from virtual_orders.evaluator.context import DEFAULT_FILL_MODEL_VERSION, SIGNAL_CALENDAR_HORIZON, build_order_context
from virtual_orders.ledger.orders import OrderRow, SignalRow, auto_order_id, find_signal_by_client_id, insert_signal
from virtual_orders.ledger.writes import persist_new_order
from virtual_orders.marketdata.calendars import calendar_for_window

REQUIRED_FIELDS = (
    "client_signal_id", "strategy", "strategy_version", "source", "ticker", "direction",
    "entry_zone_low", "entry_zone_high", "stop", "target1", "valid_sessions",
)
TEXT_FIELDS = ("client_signal_id", "strategy", "strategy_version", "source", "ticker")
OPTIONAL_TEXT_FIELDS = ("confirmation_note", "thesis")
DECIMAL_FIELDS = ("entry_zone_low", "entry_zone_high", "stop", "target1")
OPTIONAL_DECIMAL_FIELDS = ("target2", "trigger_price", "score")


class SignalValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(", ".join(errors))
        self.errors = errors


class IdempotencyConflict(Exception):
    def __init__(self, client_signal_id: str, existing_signal_id: UUID) -> None:
        super().__init__(f"IDEMPOTENCY_CONFLICT: {client_signal_id}")
        self.client_signal_id = client_signal_id
        self.existing_signal_id = existing_signal_id


@dataclass(frozen=True)
class ParsedSignal:
    client_signal_id: str
    strategy: str
    strategy_version: str
    source: str
    spec: SignalSpec
    confirmation_note: str | None
    score: Decimal | None
    thesis: str | None
    auto_order: bool


@dataclass(frozen=True)
class SignalSubmission:
    status: str
    signal_id: UUID
    auto_order_id: UUID | None


def parse_signal_body(body: Mapping[str, Any]) -> ParsedSignal:
    errors = [f"MISSING_FIELD:{name}" for name in REQUIRED_FIELDS if body.get(name) in (None, "")]
    if errors:
        raise SignalValidationError(errors)
    for name in TEXT_FIELDS:
        if not isinstance(body[name], str):
            errors.append(f"INVALID_TEXT:{name}")
    for name in OPTIONAL_TEXT_FIELDS:
        if body.get(name) is not None and not isinstance(body[name], str):
            errors.append(f"INVALID_TEXT:{name}")
    try:
        direction = Direction(body["direction"])
    except ValueError:
        errors.append("INVALID_DIRECTION")
    decimals: dict[str, Decimal | None] = {}
    for name in DECIMAL_FIELDS + OPTIONAL_DECIMAL_FIELDS:
        try:
            decimals[name] = coerce_decimal(body.get(name), name, optional=name in OPTIONAL_DECIMAL_FIELDS)
        except (TypeError, ValueError):
            errors.append(f"INVALID_DECIMAL:{name}")
    sessions = body["valid_sessions"]
    if isinstance(sessions, bool) or not isinstance(sessions, int):
        errors.append("INVALID_INTEGER:valid_sessions")
    auto_order = body.get("auto_order", True)
    if not isinstance(auto_order, bool):
        errors.append("INVALID_BOOLEAN:auto_order")
    if errors:
        raise SignalValidationError(errors)

    # DECIMAL_FIELDS are required: coerce_decimal above either populated them or raised INVALID_DECIMAL,
    # which returned via the `if errors` raise above. mypy cannot see that non-optionality, hence the casts.
    spec = SignalSpec(
        ticker=body["ticker"], direction=direction, entry_zone_low=cast(Decimal, decimals["entry_zone_low"]),
        entry_zone_high=cast(Decimal, decimals["entry_zone_high"]), stop=cast(Decimal, decimals["stop"]),
        target1=cast(Decimal, decimals["target1"]), target2=decimals["target2"],
        trigger_price=decimals["trigger_price"], valid_sessions=sessions,
    )
    level_errors = validate_signal(spec)
    if level_errors:
        raise SignalValidationError(level_errors)
    return ParsedSignal(
        client_signal_id=body["client_signal_id"], strategy=body["strategy"],
        strategy_version=body["strategy_version"], source=body["source"], spec=spec,
        confirmation_note=body.get("confirmation_note"), score=decimals["score"], thesis=body.get("thesis"),
        auto_order=auto_order,
    )


def _existing(conn: Connection, existing: SignalRow, payload_hash: str) -> SignalSubmission:
    if existing.payload_hash != payload_hash:
        raise IdempotencyConflict(existing.client_signal_id, existing.id)
    return SignalSubmission("EXISTING", existing.id, auto_order_id(conn, existing.id))


def submit_signal(
    engine: Engine,
    body: Mapping[str, Any],
    *,
    config: FillConfig,
    code_version: str,
    price_source: str,
    now: datetime | None = None,
) -> SignalSubmission:
    try:
        payload_hash = sha256_hex(dict(body))
    except (TypeError, ValueError) as exc:
        raise SignalValidationError([f"NON_CANONICAL_BODY:{exc}"]) from exc
    client_id = body.get("client_signal_id")
    if isinstance(client_id, str):
        with engine.connect() as conn:
            found = find_signal_by_client_id(conn, client_id)
            if found is not None:
                return _existing(conn, found, payload_hash)

    parsed = parse_signal_body(body)
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    calendar = calendar_for_window(created_at, created_at + SIGNAL_CALENDAR_HORIZON)
    start = evaluation_start_ts(calendar, created_at)
    until = signal_valid_until_ts(calendar, start, parsed.spec.valid_sessions)
    signal = SignalRow(
        id=uuid4(), client_signal_id=parsed.client_signal_id, payload_hash=payload_hash,
        created_at=created_at, strategy=parsed.strategy, strategy_version=parsed.strategy_version,
        source=parsed.source, spec=parsed.spec, evaluation_start_ts=start, valid_until_ts=until,
        confirmation_note=parsed.confirmation_note, score=parsed.score, thesis=parsed.thesis,
    )
    with engine.begin() as conn:
        if not insert_signal(conn, signal, body):
            raced = find_signal_by_client_id(conn, parsed.client_signal_id)
            if raced is None:
                raise RuntimeError(f"signal {parsed.client_signal_id} conflicted but is not visible")
            return _existing(conn, raced, payload_hash)
        order_id: UUID | None = None
        if parsed.auto_order:
            order = OrderRow(
                id=uuid4(), signal_id=signal.id, origin=Origin.AUTO_STRATEGY, created_at=created_at,
                evaluation_start_ts=start, valid_until_ts=until,
                fill_model_version=DEFAULT_FILL_MODEL_VERSION, config=config, code_version=code_version,
                risk_amount=config.risk_amount, price_source=price_source,
            )
            created = get_fill_model(order.fill_model_version).new_order_state(build_order_context(signal, order))
            persist_new_order(conn, order, parsed.spec.direction, created)
            order_id = order.id
    return SignalSubmission("CREATED", signal.id, order_id)
