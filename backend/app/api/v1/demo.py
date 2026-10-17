from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime

from app.api.dependencies import database_from_request, settings_from_request
from app.core.auth import Principal, require_demo
from app.core.config import Settings
from app.db.falkordb import GraphDatabase
from app.demo.dataset import dataset_definition
from app.models.events import (
    DEMO_TENANT,
    EventReceipt,
    IngestRequest,
    ReplayRequest,
    ResetRequest,
    Scenario,
)
from app.models.graph import TenantScope
from app.repositories.demo import DemoRepository
from app.schemas.contract import ScenarioCatalog, SeedResult
from app.schemas.demo import (
    CompareRequest,
    DemoStatus,
    GraphSnapshot,
    ReplayResult,
    SnapshotComparison,
)
from app.services.events import EventIngestionService

router = APIRouter(prefix="/demo", tags=["synthetic demo"])


def demo_service(
    actor: Principal,
    settings: Annotated[Settings, Depends(settings_from_request)],
    database: Annotated[GraphDatabase, Depends(database_from_request)],
) -> EventIngestionService:
    require_demo(actor)
    if not settings.demo_mode or settings.app_env == "production":
        raise HTTPException(status_code=404)
    return EventIngestionService(DemoRepository(database, TenantScope(tenant_id=DEMO_TENANT)))


DemoService = Annotated[EventIngestionService, Depends(demo_service)]


@router.post("/seed", response_model=SeedResult)
def seed(service: DemoService) -> dict:
    return service.seed()


@router.get("/scenarios", response_model=ScenarioCatalog)
def scenarios(service: DemoService) -> dict:
    definition = dataset_definition()
    return {
        "synthetic": True,
        "dataset_version": "synthetic-2026.1",
        "scenarios": {
            scenario.value: [e.model_dump(mode="json") for e in events]
            for scenario, events in definition.events.items()
        },
    }


@router.get("/scenarios/{scenario}", response_model=DemoStatus)
def status(scenario: Scenario, service: DemoService) -> DemoStatus:
    return service.status(scenario)


@router.post("/scenarios/{scenario}/reset", response_model=DemoStatus)
def reset(scenario: Scenario, body: ResetRequest, service: DemoService) -> DemoStatus:
    return service.reset(scenario, body.expected_generation)


@router.post("/scenarios/{scenario}/replay", response_model=ReplayResult)
def replay(scenario: Scenario, body: ReplayRequest, service: DemoService) -> ReplayResult:
    return service.replay(scenario, body.expected_generation, body.through)


@router.post("/events", response_model=EventReceipt)
def ingest(body: IngestRequest, service: DemoService) -> EventReceipt:
    return service.ingest(body.event, body.expected_generation)


@router.get("/scenarios/{scenario}/snapshot", response_model=GraphSnapshot)
def snapshot(
    scenario: Scenario, service: DemoService, at: Annotated[AwareDatetime | None, Query()] = None
) -> GraphSnapshot:
    return service.snapshot(scenario, at)


@router.post("/scenarios/{scenario}/compare", response_model=SnapshotComparison)
def compare(scenario: Scenario, body: CompareRequest, service: DemoService) -> SnapshotComparison:
    return service.compare(scenario, body.before, body.after)
