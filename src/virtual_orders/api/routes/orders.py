from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.evaluator.commands import cancel_order
from virtual_orders.evaluator.manual import create_manual_order

router = APIRouter()


@router.post("/signals/{signal_id}/orders")
def post_manual_order(signal_id: UUID, services: ServicesDep) -> Response:
    created = create_manual_order(
        services.engine, signal_id, config=services.fill_config, code_version=services.code_version,
        price_source=services.price_source, created_at=services.clock(), gateway=services.gateway,
    )
    return json_response({
        "order_id": created.order_id, "actionability_run_id": created.actionability_run_id,
        "data_as_of": created.data_as_of, "partial_bar_skipped": created.partial_bar_skipped,
    }, 201)


@router.post("/orders/{order_id}/cancel")
def post_cancel(order_id: UUID, services: ServicesDep) -> Response:
    outcome = cancel_order(services.engine, order_id, at=services.clock(), requested_by="user")
    return json_response({"order_id": order_id, "event_keys": list(outcome.event_keys)})
