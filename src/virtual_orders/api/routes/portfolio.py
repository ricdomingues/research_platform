"""Virtual portfolio (D45) and the disabled real portfolio slot (D46). Real and virtual totals are never mixed."""

from __future__ import annotations

from fastapi import APIRouter, Response

from virtual_orders.api.deps import ServicesDep
from virtual_orders.api.encoding import json_response
from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.portfolio.sources import PHASE_0_PENDING, SOURCE_UNAVAILABLE
from virtual_orders.readmodels.portfolio import virtual_portfolio

router = APIRouter()


@router.get("/portfolio/virtual")
def get_virtual_portfolio(services: ServicesDep) -> Response:
    as_of = acquire_data_as_of(services.engine)
    with services.engine.connect() as conn:
        portfolio = virtual_portfolio(conn, as_of=as_of)
    return json_response({"kind": "VIRTUAL", "data_as_of": as_of, "portfolio": portfolio})


@router.get("/portfolio/real")
def get_real_portfolio(services: ServicesDep) -> Response:
    source = services.portfolio_source
    if source is None:
        return json_response({"kind": "REAL", "available": False, "reason": PHASE_0_PENDING, "source": None,
                              "positions": []})
    try:
        positions = source.list_positions()
    except Exception as exc:  # noqa: BLE001 - display-only data: never a 500 and never the exception text (D46)
        return json_response({"kind": "REAL", "available": False, "reason": SOURCE_UNAVAILABLE,
                              "source": source.name, "error": type(exc).__name__, "positions": []})
    return json_response({"kind": "REAL", "available": True, "reason": None, "source": source.name,
                          "positions": positions})
