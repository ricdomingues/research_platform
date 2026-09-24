"""The manual-review panel read model (Plan 7, D103-D105).

Reads only. It never opens an `ACTIONABILITY` run, never calls the gateway and never ingests a bar (D103):
the panel is polled every few seconds, and a run per poll would inflate `evaluation_runs` until
`actionability_outcomes` (D7) and the observation report's `actionability_requests` (D70) -- both of which
count the owner's clicks -- stopped meaning anything.

The precedence below mirrors `create_manual_order` deliberately, so the panel can never contradict the write
path (D104): expiry is decided before coverage there too, and coverage is the same `STRICT_PRIMARY` policy over
the same calendar minutes. The one difference is that the write path ingests first; not ingesting can only make
this read see fewer bars, which moves a row towards STALE and never towards a green state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from types import ModuleType
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, or_, select

from core.actionability import ActionabilityReason, signal_actionability
from core.domain.models import FillConfig, OrderStatus
from core.fills import get_fill_model
from virtual_orders.evaluator.context import DEFAULT_FILL_MODEL_VERSION, signal_context
from virtual_orders.evaluator.coverage import STRICT_PRIMARY_COVERAGE, CoveragePolicy
from virtual_orders.ledger.orders import SignalRow, signal_from_row
from virtual_orders.marketdata.asof import bars_in_minutes, floor_minute, read_bars_as_of
from virtual_orders.storage.tables import order_state, orders, signals

MAX_PANEL_ROWS = 200
DEFAULT_PANEL_ROWS = 50


class PanelState(StrEnum):
    """What the owner is being told. `WATCH` is not here: it belongs to candidates, before a signal exists."""

    ACTIONABLE = "ACTIONABLE"
    EXIT = "EXIT"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"


# Reasons this read model names itself. Everything else it reports comes from the domain verbatim (D105).
COVERAGE_UNVERIFIED = "COVERAGE_UNVERIFIED"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
POSITION_OPEN = "POSITION_OPEN"
ENTRY_WINDOW_OPEN = "ENTRY_WINDOW_OPEN"

_OPEN_STATUSES = (OrderStatus.OPEN.value, OrderStatus.PARTIAL.value)

# The entry is gone, but nothing was traded on paper: the row describes a missed opportunity, not a position.
_ENTRY_GONE = frozenset({
    ActionabilityReason.STOPPED,
    ActionabilityReason.TARGET_REACHED,
    ActionabilityReason.ENTRY_OPPORTUNITY_ALREADY_OCCURRED,
})


def _live_signal_ids(since: datetime) -> Any:
    """A signal is on the panel while its entry window is open, or while an order of its own still needs the owner."""
    pending = (
        select(orders.c.id)
        .select_from(orders.join(order_state, order_state.c.order_id == orders.c.id))
        .where(
            orders.c.signal_id == signals.c.id,
            orders.c.replay.is_(False),
            or_(order_state.c.status.in_(_OPEN_STATUSES), order_state.c.needs_review.is_(True)),
        )
        .exists()
    )
    return or_(signals.c.valid_until_ts >= since, pending)


def _orders_by_signal(conn: Connection, signal_ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
    """The one order that speaks for each signal: whichever most needs the owner, then the most recent."""
    if not signal_ids:
        return {}
    rows = conn.execute(
        select(
            orders.c.id.label("order_id"), orders.c.signal_id, orders.c.origin, orders.c.created_at,
            order_state.c.status, order_state.c.needs_review, order_state.c.state_document,
        )
        .select_from(orders.outerjoin(order_state, order_state.c.order_id == orders.c.id))
        .where(orders.c.signal_id.in_(signal_ids), orders.c.replay.is_(False))
        .order_by(orders.c.created_at)
    ).mappings()
    chosen: dict[UUID, dict[str, Any]] = {}
    for mapping in rows:
        row = dict(mapping)
        signal_id = row["signal_id"]
        current = chosen.get(signal_id)
        if current is None or _order_rank(row) >= _order_rank(current):
            chosen[signal_id] = row
    return chosen


def _order_rank(row: Mapping[str, Any]) -> int:
    if row.get("needs_review"):
        return 3
    if row.get("status") in _OPEN_STATUSES:
        return 2
    if row.get("status") == OrderStatus.CLOSED.value:
        return 1
    return 0


def _order_verdict(row: Mapping[str, Any] | None) -> tuple[PanelState, str] | None:
    """EXIT is about a position that exists, so it is read from the stored order, never from the hypothetical."""
    if row is None:
        return None
    if row.get("needs_review"):
        return PanelState.EXIT, MANUAL_REVIEW_REQUIRED
    status = row.get("status")
    if status in _OPEN_STATUSES:
        return PanelState.EXIT, POSITION_OPEN
    if status == OrderStatus.CLOSED.value:
        document = row.get("state_document") or {}
        reason = document.get("close_reason")
        if reason is not None:
            return PanelState.EXIT, str(reason)
    return None


def _entry_verdict(
    conn: Connection,
    signal: SignalRow,
    *,
    as_of: datetime,
    data_as_of: datetime,
    price_source: str,
    config: FillConfig,
    policy: CoveragePolicy,
    fill_model: ModuleType,
) -> tuple[PanelState, str]:
    if as_of >= signal.valid_until_ts:
        # Checked before coverage, exactly as the write path checks it before ingest: an expired signal is
        # expired whatever the data looks like, and must not be reported as a freshness problem.
        return PanelState.EXPIRED, ActionabilityReason.SIGNAL_EXPIRED.value

    ctx = signal_context(signal, config)
    start = signal.evaluation_start_ts
    bar_end = min(floor_minute(as_of), signal.valid_until_ts)
    minutes = ctx.calendar.expected_minutes(start, bar_end) if bar_end > start else []
    bars = []
    if minutes:
        bars = bars_in_minutes(
            read_bars_as_of(conn, signal.spec.ticker, price_source, start, bar_end, data_as_of), minutes
        )
    coverage = policy.assess(price_source=price_source, ticker=signal.spec.ticker, expected=minutes, bars=bars)
    if not coverage.verified or coverage.unresolved:
        # Stale always wins a green state (D104). The same hole makes the write path answer 503.
        return PanelState.STALE, COVERAGE_UNVERIFIED

    decision = signal_actionability(fill_model, ctx, bars, as_of)
    if decision.reason is ActionabilityReason.ACTIONABLE:
        return PanelState.ACTIONABLE, ENTRY_WINDOW_OPEN
    if decision.reason is ActionabilityReason.INVALIDATED:
        return PanelState.INVALIDATED, decision.reason.value
    if decision.reason in _ENTRY_GONE:
        return PanelState.EXPIRED, decision.reason.value
    return PanelState.EXPIRED, decision.reason.value


def panel_rows(
    conn: Connection,
    *,
    as_of: datetime,
    data_as_of: datetime,
    price_source: str,
    config: FillConfig,
    since: datetime,
    limit: int = DEFAULT_PANEL_ROWS,
    policy: CoveragePolicy = STRICT_PRIMARY_COVERAGE,
) -> list[dict[str, Any]]:
    """One row per signal the owner may still have to act on, each already carrying its decided state."""
    found = conn.execute(
        select(signals)
        .where(_live_signal_ids(since))
        .order_by(signals.c.valid_until_ts.desc(), signals.c.id)
        .limit(limit)
    ).all()
    rows = [signal_from_row(row) for row in found]
    placed = _orders_by_signal(conn, [row.id for row in rows])
    fill_model = get_fill_model(DEFAULT_FILL_MODEL_VERSION)

    panel: list[dict[str, Any]] = []
    for signal in rows:
        order = placed.get(signal.id)
        verdict = _order_verdict(order)
        if verdict is None:
            verdict = _entry_verdict(
                conn, signal, as_of=as_of, data_as_of=data_as_of, price_source=price_source, config=config,
                policy=policy, fill_model=fill_model,
            )
        state, reason = verdict
        panel.append({
            "signal_id": signal.id,
            "ticker": signal.spec.ticker,
            "state": state.value,
            "reason": reason,
            "strategy": signal.strategy,
            "strategy_version": signal.strategy_version,
            "direction": signal.spec.direction.value,
            "entry_zone_low": signal.spec.entry_zone_low,
            "entry_zone_high": signal.spec.entry_zone_high,
            "stop": signal.spec.stop,
            "target1": signal.spec.target1,
            "target2": signal.spec.target2,
            "valid_until_ts": signal.valid_until_ts,
            "order_id": None if order is None else order["order_id"],
            "order_status": None if order is None else order["status"],
        })
    return panel
