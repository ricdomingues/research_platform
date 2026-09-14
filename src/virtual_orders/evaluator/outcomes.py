"""Per-order result of an evaluator operation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from virtual_orders.ledger.errors import LedgerIntegrityError

ERROR_PREFIX = "ERROR:"


@dataclass(frozen=True)
class OrderOutcome:
    order_id: UUID
    event_keys: tuple[str, ...] = ()
    segment: tuple[datetime, datetime] | None = None
    error: str | None = None


def isolated(order_id: UUID, operation: Callable[[], OrderOutcome]) -> OrderOutcome:
    """One order's failure never stops a batch: integrity errors keep their kind, anything else is `ERROR:<Type>`.

    Integrity errors are already quarantined (incident + freeze) by `run_guarded` before they reach here.
    """
    try:
        return operation()
    except LedgerIntegrityError as error:
        return OrderOutcome(order_id, error=error.kind)
    except Exception as exc:  # noqa: BLE001 - reported per order in the outcome and the run detail
        return OrderOutcome(order_id, error=f"{ERROR_PREFIX}{type(exc).__name__}")


def split_errors(outcomes: Iterable[OrderOutcome]) -> tuple[dict[str, str], dict[str, str]]:
    """(integrity_errors, order_errors) keyed by order id, for run details."""
    integrity: dict[str, str] = {}
    other: dict[str, str] = {}
    for outcome in outcomes:
        if outcome.error is None:
            continue
        target = other if outcome.error.startswith(ERROR_PREFIX) else integrity
        target[str(outcome.order_id)] = outcome.error
    return integrity, other
