"""Synthetic SOC control plane; server binds tenant and Coordinator authority."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import database_from_request, settings_from_request
from app.core.auth import Principal, require_demo
from app.core.config import Settings
from app.db.falkordb import GraphDatabase
from app.models.events import DEMO_TENANT
from app.models.graph import TenantScope, from_epoch_us
from app.models.investigation import (
    CaseActivity,
    CaseDetail,
    CaseGraph,
    CaseHandoff,
    CaseList,
    CaseMemories,
    CaseSummary,
    CaseTimeline,
    EvidenceView,
    HypothesisView,
    InvestigationStatus,
    Reconstruction,
    ReviewCaseEvidence,
    ReviewHypothesis,
    TransferCase,
    TransitionCase,
)
from app.models.workflow import AgentKey
from app.repositories.investigation import InvestigationRepository
from app.repositories.visualization import VisualizationRepository
from app.schemas.contract import PageQuery
from app.schemas.risk import GraphID
from app.services.investigation import InvestigationService

router = APIRouter(prefix="/investigations", tags=["investigation intelligence"])


def case_service(
    actor: Principal,
    settings: Annotated[Settings, Depends(settings_from_request)],
    database: Annotated[GraphDatabase, Depends(database_from_request)],
):
    require_demo(actor)
    if not settings.demo_mode or settings.app_env == "production":
        raise HTTPException(status_code=404)
    repo = InvestigationRepository(database, TenantScope(tenant_id=DEMO_TENANT), settings)
    return InvestigationService(repo, repo.agent_id(AgentKey.COORDINATOR))


Service = Annotated[InvestigationService, Depends(case_service)]


@router.get("", response_model=CaseList)
def cases(
    service: Service,
    status: InvestigationStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: GraphID | None = None,
    snapshot_sequence: Annotated[int | None, Query(ge=0, le=9007199254740991)] = None,
):
    repository = VisualizationRepository(
        service.repository.database, service.repository.scope.tenant_id
    )
    seq = repository.fence(
        PageQuery(limit=limit, cursor=cursor, snapshot_sequence=snapshot_sequence)
    )
    rows = repository.read(
        "MATCH (i:SecurityCase {tenant_id:$tenant}) WHERE i.id > $cursor "
        "AND ($status IS NULL OR i.status=$status) RETURN properties(i) ORDER BY i.id LIMIT $limit",
        {"cursor": cursor or "", "status": status, "limit": limit + 1},
    )
    result = []
    for (case,) in rows[:limit]:
        service.require(case["id"])
        result.append(
            CaseSummary(
                investigation_id=case["id"],
                severity=case["severity"],
                status=case["status"],
                revision=case["revision"],
                owner_agent_id=case["owner_agent_id"],
                created_at=from_epoch_us(case["created_at"]),
                updated_at=from_epoch_us(case["updated_at"]),
            )
        )
    repository.check(seq)
    return {
        "investigations": result,
        "count": len(result),
        "snapshot_sequence": seq,
        "next_cursor": result[-1].investigation_id if len(rows) > limit else None,
        "has_more": len(rows) > limit,
    }


@router.get("/{investigation_id}", response_model=CaseDetail)
def detail(investigation_id: GraphID, service: Service):
    return service.detail(investigation_id)


@router.get("/{investigation_id}/timeline", response_model=CaseTimeline)
def timeline(investigation_id: GraphID, service: Service):
    return {
        "investigation_id": investigation_id,
        "items": service.detail(investigation_id).historical_change_timeline,
    }


@router.get("/{investigation_id}/graph", response_model=CaseGraph)
def graph(investigation_id: GraphID, service: Service):
    return service.graph(investigation_id)


@router.get("/{investigation_id}/history", response_model=Reconstruction)
def history(investigation_id: GraphID, service: Service):
    return service.reconstruct(investigation_id)


@router.get("/{investigation_id}/memories", response_model=CaseMemories)
def memories(investigation_id: GraphID, service: Service):
    return {"investigation_id": investigation_id, "memories": service.memories(investigation_id)}


@router.get("/{investigation_id}/evidence/{evidence_id}", response_model=EvidenceView)
def evidence(investigation_id: GraphID, evidence_id: GraphID, service: Service):
    return service.evidence(investigation_id, evidence_id)


@router.get("/{investigation_id}/activity", response_model=CaseActivity)
def activity(investigation_id: GraphID, service: Service):
    case = service.detail(investigation_id)
    workflows = [service.repository.inspect(i) for i in case.workflow_ids]
    return {
        "investigation_id": investigation_id,
        "agents": case.assigned_agents,
        "completed_tasks": case.completed_tasks,
        "pending_tasks": case.pending_tasks,
        "runs": [r for workflow in workflows for r in workflow.runs],
        "audit": case.audit_history,
    }


@router.post("/{investigation_id}/transitions", response_model=CaseDetail)
def transition(investigation_id: GraphID, body: TransitionCase, service: Service):
    service.transition(investigation_id, body)
    return service.detail(investigation_id)


@router.post("/{investigation_id}/evidence/reviews", response_model=EvidenceView)
def review_evidence(investigation_id: GraphID, body: ReviewCaseEvidence, service: Service):
    return service.review_evidence(investigation_id, body)


@router.post("/{investigation_id}/hypotheses/reviews", response_model=list[HypothesisView])
def review_hypothesis(investigation_id: GraphID, body: ReviewHypothesis, service: Service):
    return service.review_hypothesis(investigation_id, body)


@router.post("/{investigation_id}/handoffs", response_model=CaseHandoff)
def transfer(investigation_id: GraphID, body: TransferCase, service: Service):
    return service.transfer(investigation_id, body)


@router.get("/{investigation_id}/handoffs/{handoff_id}", response_model=CaseHandoff)
def handoff(investigation_id: GraphID, handoff_id: GraphID, service: Service):
    return service.handoff(investigation_id, handoff_id)


@router.post("/{investigation_id}/resume", response_model=CaseDetail)
def resume(investigation_id: GraphID, service: Service):
    case = service.require(investigation_id)
    # Demo Coordinator delegates fixed read-only checks to the persisted graph owner.
    owner = InvestigationService(service.repository, case["owner_agent_id"])
    owner.resume_checks(investigation_id)
    return service.detail(investigation_id)
