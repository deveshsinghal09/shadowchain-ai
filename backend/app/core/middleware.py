import logging
from time import perf_counter
from uuid import UUID, uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response
from app.core.logging import request_id_context

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied_id = Headers(scope=scope).get("x-request-id", "")
        try:
            request_id = str(UUID(supplied_id))
        except ValueError:
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        started = perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            route = scope.get("route")
            logger.info(
                "request_completed",
                extra={
                    "method": scope["method"],
                    "route": getattr(route, "path", "unmatched"),
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
            )
            request_id_context.reset(token)


class RequestSizeMiddleware:
    """Bound request bytes inside CORS, while correlation/logging also wrap preflights."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            chunks = []
            size = 0
            maximum = scope["app"].state.settings.api_request_max_bytes
            while True:
                incoming = await receive()
                if incoming["type"] == "http.disconnect":
                    return
                size += len(incoming.get("body", b""))
                if size > maximum:
                    response = error_response(
                        Request(scope),
                        413,
                        "payload_too_large",
                        "Request body exceeds the configured byte limit",
                    )
                    await response(scope, receive, send)
                    return
                chunks.append(incoming.get("body", b""))
                if not incoming.get("more_body", False):
                    break
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {
                        "type": "http.request",
                        "body": b"".join(chunks),
                        "more_body": False,
                    }
                return await receive()

            await self.app(scope, bounded_receive, send)
        else:
            await self.app(scope, receive, send)
