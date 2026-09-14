from __future__ import annotations

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response, read_json_object
from virtual_orders.api.replay_request import parse_replay_request, run_replay

router = APIRouter()


@router.post("/replay")
async def post_replay(request: Request, services: ServicesDep) -> Response:
    replay = parse_replay_request(await read_json_object(request))
    report = await run_in_threadpool(run_replay, services, replay)
    return json_response({"run_id": report.run_id, "mode": report.mode, "created": report.created,
                          "failures": report.failures})
