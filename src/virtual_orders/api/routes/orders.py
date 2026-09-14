from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from core.domain.models import OrderStatus, Origin
from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.evaluator.commands import cancel_order
from virtual_orders.evaluator.manual import create_manual_order
from virtual_orders.readmodels.orders import OrderFilters, list_orders, order_detail

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


@router.get("/orders")
def get_orders(
    services: ServicesDep,
    status: OrderStatus | None = None,
    origin: Origin | None = None,
    strategy: str | None = None,
    replay: bool = False,
    needs_review: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    filters = OrderFilters(
        status=None if status is None else status.value, origin=None if origin is None else origin.value,
        strategy=strategy, replay=replay, needs_review=needs_review,
    )
    with services.engine.connect() as conn:
        return json_response({"orders": list_orders(conn, filters, limit=limit, offset=offset)})


@router.get("/orders/{order_id}")
def get_order_detail(order_id: UUID, services: ServicesDep) -> Response:
    with services.engine.connect() as conn:
        return json_response(order_detail(conn, order_id))
