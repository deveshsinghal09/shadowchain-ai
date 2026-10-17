"""Demo-only trusted tenant binding; no caller-selected agent/tenant or tool execution."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import database_from_request, settings_from_request
from app.core.auth import Principal, require_demo
from app.core.config import Settings
from app.db.falkordb import GraphDatabase
from app.models.events import DEMO_TENANT
from app.models.graph import TenantScope, from_epoch_us
from app.models.memory import HandoffContext, MemoryRecord
from app.models.workflow import (
    AGENT_NAMES,
    STAGES,
    WORKFLOW_VERSION,
    AgentRunRecord,
    InvestigationRecord,
    TriggerInvestigation,
)
from app.repositories.workflow import WorkflowRepository
from app.schemas.contract import AgentDefinition
from app.schemas.risk import GraphID
from app.services.workflow import InvestigationWorkflow

router = APIRouter(prefix="/agents", tags=["durable security agents"])


def workflow_service(
    actor: Principal,
    settings: Annotated[Settings, Depends(settings_from_request)],
    database: Annotated[GraphDatabase, Depends(database_from_request)],
) -> InvestigationWorkflow:
    require_demo(actor)
    if not settings.demo_mode or settings.app_env == "production":
        raise HTTPException(status_code=404)
    return InvestigationWorkflow(
        WorkflowRepository(database, TenantScope(tenant_id=DEMO_TENANT), settings)
    )


Service = Annotated[InvestigationWorkflow, Depends(workflow_service)]


@router.get("", response_model=list[AgentDefinition])
def agents(service: Service):
    stored = {node["id"]: node for node in service.repository.agents()}
    return [
        AgentDefinition(
            id=service.repository.agent_id(stage),
            name=AGENT_NAMES[stage],
            agent_type=stage,
            version=WORKFLOW_VERSION,
            registered=service.repository.agent_id(stage) in stored,
            created_at=from_epoch_us(stored[service.repository.agent_id(stage)]["created_at"])
            if service.repository.agent_id(stage) in stored
            else None,
            updated_at=from_epoch_us(stored[service.repository.agent_id(stage)]["updated_at"])
            if service.repository.agent_id(stage) in stored
            else None,
        )
        for stage in STAGES
    ]


@router.post("/investigations", response_model=InvestigationRecord)
def trigger(body: TriggerInvestigation, service: Service):
    return service.trigger(body)


@router.get("/investigations/{investigation_id}", response_model=InvestigationRecord)
def inspect(investigation_id: GraphID, service: Service):
    return service.repository.inspect(investigation_id)


@router.post("/investigations/{investigation_id}/resume", response_model=InvestigationRecord)
def resume(investigation_id: GraphID, service: Service):
    return service.resume(investigation_id)


@router.get("/runs/{run_id}", response_model=AgentRunRecord)
def run(run_id: GraphID, service: Service):
    return service.repository.inspect_run(run_id)


@router.get("/investigations/{investigation_id}/memories", response_model=list[MemoryRecord])
def memories(investigation_id: GraphID, service: Service):
    return service.shared_memories(investigation_id)


@router.get("/investigations/{investigation_id}/handoffs", response_model=list[HandoffContext])
def handoffs(investigation_id: GraphID, service: Service):
    return service.handoffs(investigation_id)
