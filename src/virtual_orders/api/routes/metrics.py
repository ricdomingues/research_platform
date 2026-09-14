from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.api.errors import ApiError
from virtual_orders.readmodels.metrics import GroupBy, metrics_by_group

router = APIRouter()


def _aware(value: datetime | None, name: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ApiError(422, "REQUEST_INVALID", detail={"errors": [{"loc": ["query", name],
                                                                   "msg": "datetime must be timezone-aware"}]})
    return value


@router.get("/metrics")
def get_metrics(
    services: ServicesDep,
    group_by: GroupBy | None = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
    replay: bool = False,
    include_needs_review: bool = False,
) -> Response:
    start, end = _aware(created_from, "from"), _aware(created_to, "to")
    with services.engine.connect() as conn:
        groups = metrics_by_group(
            conn, group_by=group_by, created_from=start, created_to=end, replay=replay,
            include_needs_review=include_needs_review, resamples=services.bootstrap_resamples,
            seed=services.bootstrap_seed,
        )
    return json_response({
        "group_by": group_by, "from": start, "to": end, "replay": replay,
        "include_needs_review": include_needs_review, "groups": groups,
    })
