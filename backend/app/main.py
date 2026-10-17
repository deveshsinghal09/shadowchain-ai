import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app import __version__
from app.api.v1.router import router
from app.core.auth import required_permissions
from app.core.config import Settings, get_settings
from app.core.errors import DatabaseUnavailable, install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestLoggingMiddleware, RequestSizeMiddleware
from app.db.falkordb import GraphDatabase
from app.repositories.foundation import FoundationRepository
from app.schemas.live import LiveAuthenticate, LiveControl, LiveError, LivePing
from app.workers.recovery import recover_workflows

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else get_settings()
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = GraphDatabase(config)
        application.state.database = database
        stop = asyncio.Event()
        recovery = None
        try:
            try:
                await run_in_threadpool(database.initialize_schema)
                await run_in_threadpool(FoundationRepository(database).ensure)
                logger.info("foundation_initialized")
            except DatabaseUnavailable:
                # Keep liveness available so orchestration can distinguish an outage
                # from a crashed process. Readiness will retry the real graph each time.
                logger.warning("startup_database_unavailable")
            if (
                config.workflow_recovery_enabled
                and config.demo_mode
                and config.app_env != "production"
            ):
                recovery = asyncio.create_task(recover_workflows(database, config, stop))
            yield
        finally:
            stop.set()
            if recovery is not None:
                await recovery
            await run_in_threadpool(database.close)
            logger.info("database_pool_closed")

    application = FastAPI(
        title="SHADOWCHAIN AI",
        description=(
            "SHADOWCHAIN API contract v1. Authenticated tenant-scoped graph intelligence, "
            "durable six-agent workflows and replayable persisted events. Findings are potential "
            "attack paths. AWS is optional and disabled by default; no IAM execution exists."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if config.app_env != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if config.app_env != "production" else None,
    )
    application.state.settings = config
    application.add_middleware(RequestSizeMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=config.cors_allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After", "WWW-Authenticate"],
    )
    application.add_middleware(RequestLoggingMiddleware)
    install_error_handlers(application)
    application.include_router(router)
    original_openapi = application.openapi

    def contract_openapi():
        schema = original_openapi()
        schema["info"]["x-contract-version"] = "1"
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if isinstance(operation, dict) and operation.get("security"):
                    operation["x-required-permissions"] = sorted(
                        required_permissions(method.upper(), path)
                    )
        schema["x-websocket-contract"] = {
            "path": "/api/v1/ws/live",
            "schema_version": "1",
            "authentication": "First text frame within five seconds; never URL query credentials",
            "client_authentication_schema": LiveAuthenticate.model_json_schema(),
            "event_page_schema": "#/components/schemas/LivePage",
            "ordering": "FalkorDB tenant sequence; after is exclusive",
            "delivery": "Replayable at-least-once; persist last processed cursor client-side",
            "max_server_frame_bytes": 65536,
        }
        for model in (LiveAuthenticate, LiveControl, LiveError, LivePing):
            definition = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, value in definition.pop("$defs", {}).items():
                schema["components"]["schemas"].setdefault(name, value)
            schema["components"]["schemas"][model.__name__] = definition
        schema["x-websocket-contract"]["control_schema"] = "#/components/schemas/LiveControl"
        schema["x-websocket-contract"]["error_schema"] = "#/components/schemas/LiveError"
        return schema

    application.openapi = contract_openapi
    return application


app = create_app()
