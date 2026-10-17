from typing import Annotated

from fastapi import APIRouter, Depends

from app import __version__
from app.api.dependencies import database_from_request, settings_from_request
from app.core.config import Settings
from app.db.falkordb import GraphDatabase
from app.schemas.errors import ErrorResponse
from app.schemas.health import HealthResponse, ReadyResponse

router = APIRouter(tags=["operations"])


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(settings_from_request)]) -> HealthResponse:
    """Process liveness; deliberately independent of downstream availability."""
    return HealthResponse(version=__version__, demo_mode=settings.demo_mode)


@router.get("/ready", response_model=ReadyResponse, responses={503: {"model": ErrorResponse}})
def ready(database: Annotated[GraphDatabase, Depends(database_from_request)]) -> ReadyResponse:
    """Readiness requires a successful Cypher query against the configured FalkorDB graph."""
    database.check_ready()
    database.initialize_schema()
    database.verify_schema()
    return ReadyResponse()
