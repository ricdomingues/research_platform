from __future__ import annotations

from fastapi import APIRouter, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.readmodels.health import HealthState, build_health_report

router = APIRouter()


@router.get("/health")
def get_health(services: ServicesDep) -> Response:
    report = build_health_report(
        services.engine, now=services.clock(), eval_interval_minutes=services.eval_interval_minutes,
    )
    status = 503 if report.state is HealthState.UNHEALTHY else 200  # UNHEALTHY = database or schema only (D17)
    return json_response({"state": report.state, "causes": report.causes, "facts": report.snapshot}, status)
