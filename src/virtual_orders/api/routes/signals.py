from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response, read_json_object
from virtual_orders.api.intake import intake_signal
from virtual_orders.readmodels.signals import list_signals

router = APIRouter()


@router.post("/signals")
async def post_signal(request: Request, services: ServicesDep) -> Response:
    body = await read_json_object(request)
    submission = await run_in_threadpool(intake_signal, services, body)
    content = {"status": submission.status, "signal_id": submission.signal_id,
               "auto_order_id": submission.auto_order_id}
    return json_response(content, 201 if submission.status == "CREATED" else 200)


@router.get("/signals")
def get_signals(
    services: ServicesDep,
    day: Annotated[date | None, Query(alias="date")] = None,
    strategy: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    with services.engine.connect() as conn:
        rows = list_signals(conn, day=day, strategy=strategy, limit=limit, offset=offset)
    return json_response({"signals": rows})
