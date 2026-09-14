"""POST /signals intake: validation, tradable-ticker gate (D14), then the idempotent service (spec 3.3)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from virtual_orders.api.errors import ApiError
from virtual_orders.evaluator.signals import (
    DECIMAL_FIELDS,
    OPTIONAL_DECIMAL_FIELDS,
    SignalSubmission,
    SignalValidationError,
    parse_signal_body,
    submit_signal,
)
from virtual_orders.ledger.orders import find_signal_by_client_id
from virtual_orders.marketdata.sources import SourceDataError, SourceUnavailable
from virtual_orders.services import Services

_DECIMAL_NAMES = frozenset(DECIMAL_FIELDS + OPTIONAL_DECIMAL_FIELDS)


def canonical_signal_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """D19: `100` and `100.0` are one price. JSON integers in decimal fields become Decimal before hashing,
    so the Plan 2 canonical hash normalizes both to the same string (spec 3.3, "decimais normalizados")."""
    return {
        name: Decimal(value) if name in _DECIMAL_NAMES and isinstance(value, int) and not isinstance(value, bool)
        else value
        for name, value in body.items()
    }


def intake_signal(services: Services, raw_body: Mapping[str, Any]) -> SignalSubmission:
    body = canonical_signal_body(raw_body)
    client_id = body.get("client_signal_id")
    known = False
    if isinstance(client_id, str):
        with services.engine.connect() as conn:
            known = find_signal_by_client_id(conn, client_id) is not None
    if not known:
        parsed = parse_signal_body(body)  # an invalid signal never reaches the provider
        ticker = parsed.spec.ticker
        try:
            status = services.ticker_check.check_ticker(ticker)
        except (SourceUnavailable, SourceDataError) as exc:
            raise ApiError(503, "TICKER_UNVERIFIABLE", detail={"ticker": ticker, "check": services.ticker_check.name,
                                                               "error": type(exc).__name__}) from exc
        if not status.tradable:
            raise SignalValidationError([f"TICKER_NOT_TRADABLE:{status.reason}"])
    return submit_signal(services.engine, body, config=services.fill_config, code_version=services.code_version,
                         price_source=services.price_source, now=services.clock())
