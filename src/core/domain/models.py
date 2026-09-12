"""Immutable domain types shared by fills, actionability, data quality and metrics."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from core.domain.calendar import CalendarRangeError, SessionCalendar
from core.domain.hashing import sha256_hex

ZERO = Decimal("0")


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class Origin(StrEnum):
    AUTO_STRATEGY = "AUTO_STRATEGY"
    MANUAL_USER = "MANUAL_USER"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELED = "CANCELED"


FINAL_STATUSES = frozenset(
    {OrderStatus.CLOSED, OrderStatus.EXPIRED, OrderStatus.INVALIDATED, OrderStatus.CANCELED}
)


class EventType(StrEnum):
    ORDER_CREATED = "ORDER_CREATED"
    TRIGGER_HIT = "TRIGGER_HIT"
    ZONE_LOST = "ZONE_LOST"
    ZONE_RECLAIMED = "ZONE_RECLAIMED"
    FILLED = "FILLED"
    TARGET1_HIT = "TARGET1_HIT"
    TARGET2_HIT = "TARGET2_HIT"
    STOPPED = "STOPPED"
    TIME_EXIT = "TIME_EXIT"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELED = "CANCELED"
    FROZEN = "FROZEN"
    DIVIDEND = "DIVIDEND"
    DATA_QUALITY = "DATA_QUALITY"
    DATA_GAP = "DATA_GAP"
    NEEDS_REVIEW = "NEEDS_REVIEW"


MARKET_EVENT_TYPES = frozenset(
    {
        EventType.TRIGGER_HIT,
        EventType.ZONE_LOST,
        EventType.ZONE_RECLAIMED,
        EventType.FILLED,
        EventType.TARGET1_HIT,
        EventType.TARGET2_HIT,
        EventType.STOPPED,
        EventType.TIME_EXIT,
        EventType.INVALIDATED,
    }
)


class CloseReason(StrEnum):
    STOPPED = "STOPPED"
    TARGET_FINAL = "TARGET_FINAL"
    TIME_EXIT = "TIME_EXIT"


class EntryPath(StrEnum):
    DIRECT = "DIRECT"
    RECLAIMED = "RECLAIMED"


class ZoneLostPolicy(StrEnum):
    RECLAIM = "RECLAIM"
    CANCEL = "CANCEL"


def _require_aware(value: datetime | None, name: str) -> None:
    if value is not None and value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


def _normalize_utc(obj: Any, *names: str) -> None:
    """Require tz-aware datetimes and store them in UTC so isoformat()-based keys are unique."""
    for name in names:
        value = getattr(obj, name)
        _require_aware(value, name)
        if value is not None:
            object.__setattr__(obj, name, value.astimezone(timezone.utc))


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = ZERO
    batch_id: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close", "volume"):
            if not isinstance(getattr(self, name), Decimal):
                raise TypeError(f"bar {name} must be Decimal")
        _require_aware(self.ts, "bar ts")
        _normalize_utc(self, "ts")
        if self.ts.second or self.ts.microsecond:
            raise ValueError("bar ts must be a whole minute")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("inconsistent OHLC")


@dataclass(frozen=True)
class SignalSpec:
    ticker: str
    direction: Direction
    entry_zone_low: Decimal
    entry_zone_high: Decimal
    stop: Decimal
    target1: Decimal
    target2: Decimal | None = None
    trigger_price: Decimal | None = None
    valid_sessions: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "direction", Direction(self.direction))

    def as_payload(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class FillConfig:
    risk_amount: Decimal = Decimal("100")
    entry_slippage_bps: Decimal = Decimal("0")
    stop_slippage_bps: Decimal = Decimal("5")
    commission_per_execution: Decimal = Decimal("0")
    sec_taf_fees_enabled: bool = False
    sec_fee_rate: Decimal = Decimal("0")
    taf_fee_per_share: Decimal = Decimal("0")
    taf_fee_max: Decimal = Decimal("0")
    zone_lost_policy: ZoneLostPolicy = ZoneLostPolicy.RECLAIM
    target1_scale_out_pct: Decimal = Decimal("50")
    data_gap_minutes: int = 30
    crosscheck_tolerance_pct: Decimal = Decimal("0.5")
    dividend_tolerance: Decimal = Decimal("0.001")

    def __post_init__(self) -> None:
        object.__setattr__(self, "zone_lost_policy", ZoneLostPolicy(self.zone_lost_policy))
        if self.risk_amount <= ZERO:
            raise ValueError("risk_amount must be positive")
        if not ZERO < self.target1_scale_out_pct < Decimal("100"):
            raise ValueError("target1_scale_out_pct must be in (0, 100)")
        for name in (
            "entry_slippage_bps", "stop_slippage_bps", "commission_per_execution", "sec_fee_rate",
            "taf_fee_per_share", "taf_fee_max", "crosscheck_tolerance_pct", "dividend_tolerance",
        ):
            if getattr(self, name) < ZERO:
                raise ValueError(f"{name} must be >= 0")
        if self.data_gap_minutes < 1:
            raise ValueError("data_gap_minutes must be >= 1")

    def snapshot(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class GatingState:
    status: OrderStatus
    zone_lost: bool
    entry_eligible_from: datetime | None
    trigger_hit_at: datetime | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "zone_lost": self.zone_lost,
            "entry_eligible_from": self.entry_eligible_from,
            "trigger_hit_at": self.trigger_hit_at,
        }


@dataclass(frozen=True)
class OrderContext:
    signal: SignalSpec
    config: FillConfig
    calendar: SessionCalendar
    evaluation_start_ts: datetime
    valid_until_ts: datetime

    def __post_init__(self) -> None:
        _normalize_utc(self, "evaluation_start_ts", "valid_until_ts")
        if self.valid_until_ts <= self.evaluation_start_ts:
            raise ValueError("valid_until_ts must be after evaluation_start_ts")
        try:
            self.calendar.next_expected_minute(
                self.calendar.last_expected_minute_before(self.valid_until_ts)
            )
        except CalendarRangeError:
            raise ValueError("calendar must cover at least one session after valid_until_ts") from None


@dataclass(frozen=True)
class OrderState:
    status: OrderStatus = OrderStatus.PENDING
    zone_lost: bool = False
    zone_ever_lost: bool = False
    entry_eligible_from: datetime | None = None
    trigger_hit_at: datetime | None = None
    entry_path: EntryPath | None = None
    avg_entry: Decimal | None = None
    initial_stop: Decimal | None = None
    stop_current: Decimal | None = None
    stop_previous: Decimal | None = None
    stop_active_from: datetime | None = None
    t1_done: bool = False
    qty_total: Decimal = ZERO
    qty_open: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    costs: Decimal = ZERO
    dividends: Decimal = ZERO
    best_price: Decimal | None = None
    worst_price: Decimal | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    final_event_ts: datetime | None = None
    last_bar_ts: datetime | None = None
    close_reason: CloseReason | None = None
    frozen: bool = False
    review_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _normalize_utc(
            self, "entry_eligible_from", "trigger_hit_at", "stop_active_from", "opened_at",
            "closed_at", "final_event_ts", "last_bar_ts",
        )

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)

    def gating_state(self) -> GatingState:
        return GatingState(self.status, self.zone_lost, self.entry_eligible_from, self.trigger_hit_at)


@dataclass(frozen=True)
class Event:
    type: EventType
    event_key: str
    bar_ts: datetime | None = None
    price: Decimal | None = None
    qty: Decimal | None = None
    bar_batch_id: UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _normalize_utc(self, "bar_ts")
        if self.bar_ts is not None and (self.bar_ts.second or self.bar_ts.microsecond):
            raise ValueError("bar_ts must be a whole minute")
        if self.type in MARKET_EVENT_TYPES and self.bar_ts is None:
            raise ValueError(f"{self.type} requires bar_ts")

    def hash_material(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "bar_ts": self.bar_ts,
            "price": self.price,
            "qty": self.qty,
            "bar_batch_id": self.bar_batch_id,
            "payload": self.payload,
        }

    @property
    def payload_hash(self) -> str:
        return sha256_hex(self.hash_material())


@dataclass(frozen=True)
class StepResult:
    state: OrderState
    events: tuple[Event, ...] = ()
