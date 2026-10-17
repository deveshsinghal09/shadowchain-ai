"""Local synthetic API scope; a future authenticated API must bind its own TenantScope."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import database_from_request, settings_from_request
from app.core.auth import Principal, require_demo
from app.core.config import Settings
from app.db.falkordb import GraphDatabase
from app.models.events import DEMO_TENANT
from app.models.graph import TenantScope
from app.repositories.risk import RiskRepository
from app.schemas.risk import (
    DemoAnalysisRequest,
    DemoDifferenceRequest,
    DemoTimelineRequest,
    DemoWhatIfRequest,
    DiscoveryResult,
    FindingEvidence,
    GraphID,
    RemediationResult,
    RiskDifference,
    RiskTimeline,
)
from app.services.risk import RiskEngine

router = APIRouter(prefix="/risk", tags=["potential attack paths"])


def risk_engine(
    actor: Principal,
    settings: Annotated[Settings, Depends(settings_from_request)],
    database: Annotated[GraphDatabase, Depends(database_from_request)],
) -> RiskEngine:
    require_demo(actor)
    if not settings.demo_mode or settings.app_env == "production":
        raise HTTPException(status_code=404)
    return RiskEngine(RiskRepository(database, TenantScope(tenant_id=DEMO_TENANT)))


Engine = Annotated[RiskEngine, Depends(risk_engine)]


@router.post("/paths/discover", response_model=DiscoveryResult)
def discover(body: DemoAnalysisRequest, engine: Engine) -> DiscoveryResult:
    return engine.discover(body)


@router.post("/paths/historical", response_model=DiscoveryResult)
def historical(body: DemoAnalysisRequest, engine: Engine) -> DiscoveryResult:
    return engine.discover(body)


@router.post("/timeline", response_model=RiskTimeline)
def timeline(body: DemoTimelineRequest, engine: Engine) -> RiskTimeline:
    return engine.timeline(body)


@router.post("/differences", response_model=RiskDifference)
def differences(body: DemoDifferenceRequest, engine: Engine) -> RiskDifference:
    return engine.difference(body)


@router.post("/what-if", response_model=RemediationResult)
def what_if(body: DemoWhatIfRequest, engine: Engine) -> RemediationResult:
    return engine.what_if(body)


@router.get("/findings/{finding_id}/evidence", response_model=FindingEvidence)
def evidence(finding_id: GraphID, engine: Engine) -> FindingEvidence:
    return engine.repository.evidence(finding_id)


@router.get("/analyses/{analysis_id}", response_model=DiscoveryResult)
def saved(analysis_id: GraphID, engine: Engine) -> DiscoveryResult:
    return engine.repository.saved(analysis_id)
