from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.services.order_service import OrderError


def error_body(code: str, message: str, details: Any = None, request_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "kind": kind, "message": message, "details": details or {}, "request_id": request_id or uuid.uuid4().hex[:12]}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(OrderError)
    async def _order_error(_: Request, exc: OrderError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=error_body(exc.code, exc.message, exc.details, kind=exc.kind))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=error_body("validation_error", "request validation failed", exc.errors(), kind="DATA_INCOMPLETE"))

    @app.exception_handler(ValueError)
    async def _value(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content=error_body("invalid_value", str(exc)))
