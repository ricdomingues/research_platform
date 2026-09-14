"""Health page reads (D42): health_state_log, alert delivery state (never documents), coverage and DATA_GAP."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.readmodels.observability import OutboxOutcome, alert_outbox_view, health_log, quality_overview

router = APIRouter()
MAX_LIMIT = 500


@router.get("/health/log")
def get_health_log(services: ServicesDep, limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 20) -> Response:
    with services.engine.connect() as conn:
        return json_response({"entries": health_log(conn, limit=limit)})


@router.get("/alert-outbox")
def get_alert_outbox(
    services: ServicesDep,
    outcome: OutboxOutcome | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
) -> Response:
    with services.engine.connect() as conn:
        return json_response({"alerts": alert_outbox_view(conn, outcome=outcome, limit=limit)})


@router.get("/quality/overview")
def get_quality_overview(services: ServicesDep, limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100) -> Response:
    with services.engine.connect() as conn:
        return json_response(quality_overview(conn, limit=limit))
