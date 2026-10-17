"""Explicit local demo human review; approval has no execution capability."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import database_from_request, settings_from_request
from app.core.auth import Principal, require_demo
from app.core.config import Settings
from app.db.falkordb import GraphDatabase
from app.models.events import DEMO_TENANT
from app.models.graph import NodeKind, TenantScope
from app.models.remediation import (
    Proposal,
    ProposalReview,
    ProposeRequest,
    Recommendations,
    RecommendRequest,
    Simulation,
    SimulationRequest,
)
from app.models.workflow import AgentKey
from app.repositories.remediation import RemediationRepository
from app.repositories.workflow import WorkflowRepository
from app.schemas.risk import GraphID
from app.services.memory_access import node_id
from app.services.remediation import RemediationService

router = APIRouter(prefix="/remediation", tags=["remediation simulation, no execution"])


def remediation_service(
    actor: Principal,
    settings: Annotated[Settings, Depends(settings_from_request)],
    database: Annotated[GraphDatabase, Depends(database_from_request)],
):
    require_demo(actor)
    if not settings.demo_mode or settings.app_env == "production":
        raise HTTPException(status_code=404)
    scope = TenantScope(tenant_id=DEMO_TENANT)
    coordinator = WorkflowRepository(database, scope, settings).agent_id(AgentKey.COORDINATOR)
    space = node_id(DEMO_TENANT, NodeKind.MEMORY_SPACE, "space/workflow-team")
    return RemediationService(
        RemediationRepository(database, scope, settings, space, coordinator),
        human_reviewer_id=actor.principal_id,
    )


Service = Annotated[RemediationService, Depends(remediation_service)]


@router.post("/recommendations", response_model=Recommendations)
def recommend(body: RecommendRequest, service: Service):
    return service.recommend(body)


@router.post("/proposals", response_model=Proposal)
def propose(body: ProposeRequest, service: Service):
    return service.propose(body)


@router.get("/proposals", response_model=list[Proposal])
def proposals(service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    service.require()
    return [service.get(node["id"]) for node in service.repository.proposals(limit)]


@router.get("/proposals/{proposal_id}", response_model=Proposal)
def proposal(proposal_id: GraphID, service: Service):
    return service.get(proposal_id)


@router.post("/proposals/{proposal_id}/simulations", response_model=Simulation)
def simulate(proposal_id: GraphID, body: SimulationRequest, service: Service):
    return service.simulate(proposal_id, body)


@router.get("/simulations/{simulation_id}", response_model=Simulation)
def simulation(simulation_id: GraphID, service: Service):
    return service.saved_simulation(simulation_id)


@router.post("/proposals/{proposal_id}/reviews", response_model=Proposal)
def review(proposal_id: GraphID, body: ProposalReview, service: Service):
    return service.review(proposal_id, body)
