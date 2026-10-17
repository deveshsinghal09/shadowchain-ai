"""Authenticated replay of same-transaction persisted events, with bounded polling/backpressure."""

import asyncio
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core.auth import AccessDenied, authenticate
from app.core.errors import AppError
from app.repositories.live import CursorConflict, LiveRepository
from app.schemas.errors import ErrorDetail
from app.schemas.live import LiveAuthenticate, LiveControl, LiveError, LivePing

router = APIRouter()


@router.websocket("/ws/live")
async def live(websocket: WebSocket):
    settings = websocket.app.state.settings
    await websocket.accept()

    async def send(payload):
        encoded = payload.model_dump_json(exclude_none=True)
        if len(encoded.encode()) > 65536:
            raise ValueError("Bounded frame exceeded")
        await asyncio.wait_for(
            websocket.send_text(encoded), timeout=settings.ws_send_timeout_seconds
        )

    try:
        origin = websocket.headers.get("origin")
        if websocket.query_params or (origin is not None and origin not in settings.cors_origins):
            raise AccessDenied()
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        if len(raw.encode()) > 4096:
            raise ValueError("Authentication frame too large")
        hello = LiveAuthenticate.model_validate_json(raw)
        actor = authenticate(settings, hello.token.get_secret_value())
        if "read" not in actor.permissions:
            raise AccessDenied()
        repository = LiveRepository(websocket.app.state.database, actor.tenant_id)
        await run_in_threadpool(repository.budget, actor.principal_id, 10, category="ws.connect")
        page = await run_in_threadpool(repository.page, hello.after, hello.limit)
        await send(
            LiveControl(type="hello", cursor=hello.after, high_watermark=page.high_watermark)
        )
        cursor = hello.after
        start = monotonic()
        heartbeat = start
        while monotonic() - start < 3600:
            iteration_started = monotonic()
            # Config reload/restart closes connections; validate the current in-process policy too.
            current = authenticate(settings, hello.token.get_secret_value())
            if current.tenant_id != actor.tenant_id or "read" not in current.permissions:
                raise AccessDenied()
            if page.events:
                await send(page)
                cursor = page.cursor
            delay = 0.05 if page.has_more else settings.ws_poll_seconds
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=delay)
                if len(message) > 64:
                    raise ValueError("Only a ping control message is accepted")
                LivePing.model_validate_json(message)
                await send(LiveControl(type="pong", cursor=cursor))
            except TimeoutError:
                if monotonic() - heartbeat >= 15:
                    await send(LiveControl(type="heartbeat", cursor=cursor))
                    heartbeat = monotonic()
            # Incoming pings must not turn the receive timeout into an unbounded
            # graph-query loop. Enforce the same cadence for active and idle peers.
            await asyncio.sleep(max(0, delay - (monotonic() - iteration_started)))
            page = await run_in_threadpool(repository.page, cursor, hello.limit)
        await websocket.close(code=4000, reason="Reconnect and authenticate")
    except WebSocketDisconnect:
        return
    except (TimeoutError, AppError, ValueError, TypeError, KeyError) as exc:
        code = exc.code if isinstance(exc, AppError) else "invalid_stream_request"
        message = (
            exc.message if isinstance(exc, AppError) else "Invalid or timed-out stream request"
        )
        close_code = (
            4401
            if code == "authentication_required"
            else (
                4403
                if isinstance(exc, AccessDenied)
                else 4409
                if isinstance(exc, CursorConflict)
                else 1013
                if isinstance(exc, AppError) and exc.status_code in {429, 503}
                else 4400
            )
        )
        try:
            error = LiveError(
                error=ErrorDetail(code=code, message=message), request_id=str(uuid4())
            )
            await send(error)
            await websocket.close(code=close_code)
        except (TimeoutError, RuntimeError, WebSocketDisconnect):
            return
