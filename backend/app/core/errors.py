import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.logging import request_id_context
from app.schemas.errors import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    message = "An internal error occurred"

    def __init__(self) -> None:
        super().__init__(self.message)


class DatabaseUnavailable(AppError):
    status_code = 503
    code = "dependency_unavailable"
    message = "FalkorDB is unavailable"


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = request_id
    if status_code == 503:
        response_headers["Retry-After"] = "3"
    if status_code == 429:
        response_headers["Retry-After"] = "60"
    if status_code == 401:
        response_headers["WWW-Authenticate"] = "Bearer"
    # Unexpected-error handling runs outside user middleware. Preserve the configured
    # origin boundary so browsers can read its safe envelope as well.
    config = request.app.state.settings
    origin = request.headers.get("origin")
    if origin and origin in config.cors_origins:
        response_headers["Access-Control-Allow-Origin"] = origin
        response_headers["Vary"] = "Origin"
        response_headers["Access-Control-Expose-Headers"] = (
            "X-Request-ID, Retry-After, WWW-Authenticate"
        )
        if config.cors_allow_credentials:
            response_headers["Access-Control-Allow-Credentials"] = "true"
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details or []), request_id=request_id
    )
    return JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=response_headers
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()
        ]
        return error_response(
            request, 422, "validation_error", "Request validation failed", details
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        # Do not reflect arbitrary exception details (which may contain secrets).
        return error_response(
            request,
            exc.status_code,
            "http_error",
            "Request could not be processed",
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        token = request_id_context.set(getattr(request.state, "request_id", None))
        try:
            logger.error("unhandled_error", extra={"error_type": type(exc).__name__})
        finally:
            request_id_context.reset(token)
        return error_response(request, 500, "internal_error", "An internal error occurred")
