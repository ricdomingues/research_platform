"""Error envelope and the mapping of domain exceptions to HTTP (spec 5.1, 6; D19)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from virtual_orders.evaluator.commands import OrderAlreadyFinal, ReplayOrderReadOnly
from virtual_orders.evaluator.manual import ACTIONABILITY_UNVERIFIABLE, ManualOrderError
from virtual_orders.evaluator.replay import ReplaySelectionError
from virtual_orders.evaluator.signals import IdempotencyConflict, SignalValidationError
from virtual_orders.ledger.errors import LedgerIntegrityError, OrderNotFound, SignalNotFound


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, reason: str | None = None, detail: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.reason = reason
        self.detail: dict[str, Any] = dict(detail or {})


HTTP_ERROR_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


def to_api_error(exc: Exception, method: str) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, SignalValidationError):
        return ApiError(422, "SIGNAL_VALIDATION_FAILED", detail={"errors": exc.errors})
    if isinstance(exc, IdempotencyConflict):
        return ApiError(409, "IDEMPOTENCY_CONFLICT", detail={
            "client_signal_id": exc.client_signal_id, "existing_signal_id": exc.existing_signal_id,
        })
    if isinstance(exc, ManualOrderError):
        status = 503 if exc.code == ACTIONABILITY_UNVERIFIABLE else 422
        return ApiError(status, exc.code, exc.reason, exc.detail)
    if isinstance(exc, SignalNotFound):
        return ApiError(404, "SIGNAL_NOT_FOUND")
    if isinstance(exc, OrderNotFound):
        return ApiError(404, "ORDER_NOT_FOUND")
    if isinstance(exc, OrderAlreadyFinal):
        return ApiError(409, "ORDER_ALREADY_FINAL", detail={"order_id": exc.order_id})
    if isinstance(exc, ReplayOrderReadOnly):
        return ApiError(409, "REPLAY_ORDER_READ_ONLY", detail={"order_id": exc.order_id})
    if isinstance(exc, ReplaySelectionError):
        return ApiError(422, "REPLAY_REQUEST_INVALID", detail={"errors": [str(exc)]})
    if isinstance(exc, LedgerIntegrityError):
        # A command meets ledger state that forbids the write (409); a read meets corrupted stored data (500).
        status = 500 if method == "GET" else 409
        return ApiError(status, "INTEGRITY_ERROR", exc.kind, {"order_id": exc.order_id})
    if isinstance(exc, RequestValidationError):
        return ApiError(422, "REQUEST_INVALID", detail={"errors": [
            {"loc": [str(part) for part in error.get("loc", ())], "msg": str(error.get("msg", ""))}
            for error in exc.errors()
        ]})
    if isinstance(exc, StarletteHTTPException):
        return ApiError(exc.status_code, HTTP_ERROR_CODES.get(exc.status_code, f"HTTP_{exc.status_code}"))
    return ApiError(500, "INTERNAL_ERROR", detail={"type": type(exc).__name__})  # never the message


HANDLED = (
    ApiError, SignalValidationError, IdempotencyConflict, ManualOrderError, SignalNotFound, OrderNotFound,
    OrderAlreadyFinal, ReplayOrderReadOnly, ReplaySelectionError, LedgerIntegrityError, RequestValidationError,
    StarletteHTTPException, Exception,
)


def install_error_handlers(app: FastAPI) -> None:
    from virtual_orders.api.encoding import json_response  # encoding imports ApiError from this module

    async def handle(request: Request, exc: Exception) -> Response:
        error = to_api_error(exc, request.method)
        response = json_response({"error": {"code": error.code, "reason": error.reason, "detail": error.detail}},
                                 error.status_code)
        if isinstance(exc, StarletteHTTPException) and exc.headers:
            response.headers.update(exc.headers)  # e.g. Allow on 405
        return response

    for exception_type in HANDLED:
        app.add_exception_handler(exception_type, handle)
