from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response, read_json_object
from virtual_orders.api.errors import ApiError
from virtual_orders.api.intake import intake_signal
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.actionability import DEFAULT_PANEL_ROWS, MAX_PANEL_ROWS, panel_rows
from virtual_orders.readmodels.market import market_day_start
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


@router.get("/signals/actionability")
def get_signals_actionability(
    services: ServicesDep,
    as_of: datetime | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PANEL_ROWS)] = DEFAULT_PANEL_ROWS,
) -> Response:
    """The manual-review panel (D103): a read that opens no run, calls no gateway and ingests nothing."""
    for name, value in (("as_of", as_of), ("since", since)):
        if value is not None and value.tzinfo is None:
            raise ApiError(422, "VALIDATION_ERROR", detail={"errors": [f"NAIVE_DATETIME:{name}"]})
    at = as_of if as_of is not None else services.clock()
    window_start = since if since is not None else market_day_start(at)
    data_as_of = acquire_data_as_of(services.engine)
    with services.engine.connect() as conn:
        rows = panel_rows(
            conn, as_of=at, data_as_of=data_as_of, price_source=services.price_source,
            config=services.fill_config, since=window_start, limit=limit,
        )
    return json_response({"as_of": at, "since": window_start, "data_as_of": data_as_of, "signals": rows})
