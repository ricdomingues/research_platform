"""Daily paper-observation report (Plan 4, D60-D70): stored data only, as of the request; never a provider call."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.api.errors import ApiError
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.readmodels.observation import (
    build_observation_report,
    build_observation_summary,
    observation_snapshot,
)
from virtual_orders.readmodels.observation_window import ObservationRequestInvalid, session_window, summary_sessions

router = APIRouter()
OBSERVATION_ERROR = "OBSERVATION_REQUEST_INVALID"


def _invalid(exc: ObservationRequestInvalid) -> ApiError:
    return ApiError(422, OBSERVATION_ERROR, detail={"errors": list(exc.codes)})


@router.get("/observation/report")
def get_observation_report(services: ServicesDep, day: date) -> Response:
    as_of = acquire_data_as_of(services.engine)  # D61: the database clock at the request
    try:
        window = session_window(day, as_of)
    except ObservationRequestInvalid as exc:
        raise _invalid(exc) from None
    with observation_snapshot(services.engine) as conn:  # D61: one read-only REPEATABLE READ snapshot
        return json_response(build_observation_report(conn, window))


@router.get("/observation/summary")
def get_observation_summary(
    services: ServicesDep,
    first: Annotated[date, Query(alias="from")],
    last: Annotated[date, Query(alias="to")],
) -> Response:
    as_of = acquire_data_as_of(services.engine)
    try:
        windows = summary_sessions(first, last, as_of)
    except ObservationRequestInvalid as exc:
        raise _invalid(exc) from None
    with observation_snapshot(services.engine) as conn:  # every session of the range in the same snapshot
        return json_response(build_observation_summary(conn, windows))
