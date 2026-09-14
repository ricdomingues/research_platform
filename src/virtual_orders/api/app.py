"""FastAPI application (spec 5.1). Every route requires X-API-Key; docs endpoints are disabled (D19)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from virtual_orders.api.auth import require_api_key
from virtual_orders.api.errors import install_error_handlers
from virtual_orders.api.routes import health, metrics, orders, replay, signals, watchlist
from virtual_orders.services import Services


def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        services.close()

    app = FastAPI(
        title="Virtual Order Engine", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
        dependencies=[Depends(require_api_key(services.api_key))],
    )
    app.state.services = services
    install_error_handlers(app)
    app.include_router(signals.router)
    app.include_router(orders.router)
    app.include_router(replay.router)
    app.include_router(metrics.router)
    app.include_router(health.router)
    app.include_router(watchlist.router)
    return app
