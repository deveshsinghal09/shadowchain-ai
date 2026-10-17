"""Local demo binding only. Actor, tenant and memory space are never body/header parameters."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import database_from_request, settings_from_request
from app.core.auth import Principal, require_demo
from app.core.config import Settings
from app.db.cypher import stable_id
from app.db.falkordb import GraphDatabase
from app.models.events import DEMO_TENANT, SOURCE
from app.models.graph import NodeKind, TenantScope
from app.models.memory import (
    AgentContext,
    HandoffContext,
    HandoffRequest,
    MemoryConflict,
    MemoryQuery,
    MemoryRecord,
    MemoryWrite,
    ReviewMemory,
    SupersedeMemory,
)
from app.repositories.memory import MemoryRepository
from app.schemas.risk import GraphID
from app.services.memory import MemoryService
from app.services.memory_access import node_id

DEMO_SPACE_ID = node_id(DEMO_TENANT, NodeKind.MEMORY_SPACE, "space/security-demo")
DEMO_AGENT_ID = stable_id(DEMO_TENANT, "Agent", "", SOURCE, "agent/scout")
DEMO_REVIEWER_ID = stable_id(DEMO_TENANT, "Agent", "", SOURCE, "agent/verifier")
router = APIRouter(prefix="/memory", tags=["persistent graph memory"])


def memory_service(
    actor: Principal,
    settings: Annotated[Settings, Depends(settings_from_request)],
    database: Annotated[GraphDatabase, Depends(database_from_request)],
) -> MemoryService:
    require_demo(actor)
    if not settings.demo_mode or settings.app_env == "production":
        raise HTTPException(status_code=404)
    return MemoryService(
        MemoryRepository(database, TenantScope(tenant_id=DEMO_TENANT), DEMO_SPACE_ID, DEMO_AGENT_ID)
    )


Service = Annotated[MemoryService, Depends(memory_service)]


def reviewer_service(service: Service) -> MemoryService:
    # The trusted demo bootstrap grants Verifier the curator role. The HTTP
    # principal still needs review permission; request text cannot select an actor.
    repository = service.repository
    return MemoryService(
        MemoryRepository(
            repository.database, repository.scope, repository.space_id, DEMO_REVIEWER_ID
        )
    )


Reviewer = Annotated[MemoryService, Depends(reviewer_service)]


@router.post("/write", response_model=MemoryRecord)
def write(body: MemoryWrite, service: Service) -> MemoryRecord:
    return service.write_memory(body)


@router.post("/retrieve", response_model=list[MemoryRecord])
def retrieve(body: MemoryQuery, service: Service) -> list[MemoryRecord]:
    return service.retrieve_related_memories(body)


@router.post("/context", response_model=AgentContext)
def context(body: MemoryQuery, service: Service) -> AgentContext:
    return service.get_agent_context(body)


@router.post("/supersede", response_model=MemoryRecord)
def supersede(body: SupersedeMemory, service: Service) -> MemoryRecord:
    return service.supersede_memory(body)


@router.post("/review", response_model=MemoryRecord)
def review(body: ReviewMemory, service: Reviewer) -> MemoryRecord:
    return service.review_memory(body)


@router.post("/conflicts", response_model=list[MemoryConflict])
def conflicts(body: MemoryQuery, service: Service) -> list[MemoryConflict]:
    return service.detect_memory_conflicts(body)


@router.get("/investigations/{investigation_id}/history", response_model=list[MemoryRecord])
def investigation_history(investigation_id: GraphID, service: Service) -> list[MemoryRecord]:
    return service.retrieve_investigation_history(investigation_id)


@router.post("/handoffs", response_model=HandoffContext)
def handoff(body: HandoffRequest, service: Service) -> HandoffContext:
    return service.create_handoff_context(body)


@router.get("/handoffs/{handoff_id}", response_model=HandoffContext)
def handoff_context(handoff_id: GraphID, service: Service) -> HandoffContext:
    return service.get_handoff_context(handoff_id)
