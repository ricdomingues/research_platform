"""POST /replay body (spec 5.1): shape checks here; selection and override rules stay in the service (D15)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from virtual_orders.api.errors import ApiError
from virtual_orders.evaluator.replay import ReplayMode, ReplayReport, recalculate_orders, reproduce_orders
from virtual_orders.ledger.errors import REPRODUCE_DIVERGENCE
from virtual_orders.readmodels.incidents import latest_incidents
from virtual_orders.services import Services

ALLOWED_FIELDS = frozenset({"mode", "order_ids", "from", "to", "fill_model_version", "config_overrides", "data_as_of"})
RECALCULATE_ONLY = ("fill_model_version", "config_overrides", "data_as_of")


@dataclass(frozen=True)
class ReplayRequest:
    mode: ReplayMode
    order_ids: tuple[UUID, ...] | None
    created_from: datetime | None
    created_to: datetime | None
    fill_model_version: str | None
    config_overrides: dict[str, Any] | None
    data_as_of: datetime | None


def _invalid(errors: list[str]) -> ApiError:
    return ApiError(422, "REPLAY_REQUEST_INVALID", detail={"errors": errors})


def _instant(body: Mapping[str, Any], name: str, errors: list[str]) -> datetime | None:
    raw = body.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str):
        errors.append(f"INVALID_DATETIME:{name}")
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        errors.append(f"INVALID_DATETIME:{name}")
        return None
    if value.tzinfo is None:
        errors.append(f"NAIVE_DATETIME:{name}")
        return None
    return value


def _order_ids(body: Mapping[str, Any], errors: list[str]) -> tuple[UUID, ...] | None:
    raw = body.get("order_ids")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        errors.append("INVALID_ORDER_IDS")
        return None
    try:
        return tuple(UUID(item) for item in raw)
    except ValueError:
        errors.append("INVALID_ORDER_IDS")
        return None


def parse_replay_request(body: Mapping[str, Any]) -> ReplayRequest:
    errors = [f"UNKNOWN_FIELD:{name}" for name in sorted(set(body) - ALLOWED_FIELDS)]
    mode: ReplayMode | None = None
    raw_mode = body.get("mode")
    if isinstance(raw_mode, str) and raw_mode in ReplayMode.__members__:
        mode = ReplayMode(raw_mode)
    else:
        errors.append("INVALID_MODE")
    order_ids = _order_ids(body, errors)
    created_from = _instant(body, "from", errors)
    created_to = _instant(body, "to", errors)
    data_as_of = _instant(body, "data_as_of", errors)
    version = body.get("fill_model_version")
    if version is not None and not isinstance(version, str):
        errors.append("INVALID_TEXT:fill_model_version")
    overrides = body.get("config_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        errors.append("INVALID_OBJECT:config_overrides")
    if mode is ReplayMode.REPRODUCE:
        errors.extend(f"NOT_ALLOWED_FOR_REPRODUCE:{name}" for name in RECALCULATE_ONLY if body.get(name) is not None)
    if errors or mode is None:
        raise _invalid(errors)
    return ReplayRequest(
        mode, order_ids, created_from, created_to,
        version if isinstance(version, str) else None,
        overrides if isinstance(overrides, dict) else None,
        data_as_of,
    )


def _divergence(services: Services, report: ReplayReport, diverged_ids: list[UUID]) -> ApiError:
    """Spec 6: a divergent REPRODUCE is an integrity error with the event diff, reported per order (D15)."""
    with services.engine.connect() as conn:
        incidents = latest_incidents(conn, kind=REPRODUCE_DIVERGENCE, order_ids=diverged_ids)
    diverged: dict[UUID, dict[str, Any]] = {}
    for source in diverged_ids:
        incident = incidents.get(source)
        diverged[source] = {
            "incident_id": None if incident is None else incident.incident_id,
            "reason": None if incident is None else incident.detail.get("reason"),
            "diff": [] if incident is None else incident.detail.get("diff", []),
        }
    return ApiError(409, "REPRODUCE_DIVERGED", detail={
        "run_id": report.run_id, "mode": report.mode, "identical": report.created, "diverged": diverged,
        "failures": {source: reason for source, reason in report.failures.items() if reason != REPRODUCE_DIVERGENCE},
    })


def run_replay(services: Services, request: ReplayRequest) -> ReplayReport:
    """ReplaySelectionError (selection, overrides, data_as_of guards) propagates to the global 422 handler."""
    order_ids = None if request.order_ids is None else list(request.order_ids)
    if request.mode is ReplayMode.RECALCULATE:
        return recalculate_orders(
            services.engine, code_version=services.code_version, order_ids=order_ids,
            created_from=request.created_from, created_to=request.created_to,
            fill_model_version=request.fill_model_version, config_overrides=request.config_overrides,
            data_as_of=request.data_as_of,
        )
    report = reproduce_orders(services.engine, code_version=services.code_version, order_ids=order_ids,
                              created_from=request.created_from, created_to=request.created_to)
    diverged_ids = [source for source, reason in report.failures.items() if reason == REPRODUCE_DIVERGENCE]
    if diverged_ids:
        raise _divergence(services, report, diverged_ids)
    return report
