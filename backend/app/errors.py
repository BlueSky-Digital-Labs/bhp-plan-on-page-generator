"""Shared exception translation: every API failure becomes one JSON envelope.

The envelope carries a fixed generic message and a correlation reference. It
never includes exception text, stack traces, request bodies or configuration,
because later features will handle confidential user content.
"""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")

CORRELATION_HEADER = "X-Correlation-ID"

GENERIC_MESSAGES = {
    400: "The request could not be processed.",
    401: "Authentication is required.",
    403: "You do not have access to this resource.",
    404: "The requested resource was not found.",
    405: "That action is not supported.",
    422: "The request could not be processed.",
    429: "Too many requests. Please try again shortly.",
}
INTERNAL_MESSAGE = "Something went wrong. Please try again."


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def error_response(status_code: int, code: str, message: str, correlation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "correlation_id": correlation_id}},
        headers={CORRELATION_HEADER: correlation_id},
    )


def correlation_id_of(request: Request) -> str:
    return getattr(request.state, "correlation_id", None) or new_correlation_id()


def _code_for(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        422: "invalid_request",
        429: "rate_limited",
    }.get(status_code, "internal_error" if status_code >= 500 else "request_failed")


async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    cid = correlation_id_of(request)
    status = exc.status_code
    fallback = INTERNAL_MESSAGE if status >= 500 else GENERIC_MESSAGES[400]
    message = GENERIC_MESSAGES.get(status, fallback)
    logger.warning("http_error", extra={"correlation_id": cid, "status": status})
    return error_response(status, _code_for(status), message, cid)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default 422 body echoes the submitted input; never do that.
    cid = correlation_id_of(request)
    logger.warning("validation_error", extra={"correlation_id": cid, "status": 422})
    return error_response(422, "invalid_request", GENERIC_MESSAGES[422], cid)


def unexpected_error_response(request_scope_state: dict, exc: Exception) -> JSONResponse:
    cid = request_scope_state.get("correlation_id") or new_correlation_id()
    # Exception class only: messages can contain submitted text.
    logger.error(
        "unhandled_exception",
        extra={"correlation_id": cid, "status": 500, "exception_type": type(exc).__name__},
    )
    return error_response(500, "internal_error", INTERNAL_MESSAGE, cid)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
