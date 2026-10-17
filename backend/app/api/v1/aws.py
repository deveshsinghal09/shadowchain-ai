"""Local explicit AWS controls. No AWS SDK invocation occurs during dependency creation."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import database_from_request, settings_from_request
from app.api.v1.investigations import case_service
from app.core.auth import AccessDenied, Principal
from app.core.config import Settings
from app.db.errors import GraphNotFound
from app.db.falkordb import GraphDatabase
from app.models.aws import (
    AwsSyncRequest,
    AwsSyncV1,
    CloudTrailRequest,
    ReportRequest,
    ReportResult,
    SyncResult,
)
from app.models.graph import TenantScope
from app.repositories.aws import AwsRepository
from app.schemas.contract import AwsStatus
from app.schemas.risk import GraphID
from app.services.aws_sync import AwsSyncService
from app.services.reports import ReportService

router = APIRouter(tags=["optional AWS and redacted reports"])


def local_settings(settings: Annotated[Settings, Depends(settings_from_request)]):
    if settings.app_env == "production":
        raise HTTPException(status_code=404)
    return settings


Local = Annotated[Settings, Depends(local_settings)]
Database = Annotated[GraphDatabase, Depends(database_from_request)]


@router.get("/aws/status", response_model=AwsStatus)
def status(settings: Local):
    return {
        "aws_enabled": settings.aws_enabled,
        "cloudtrail_enabled": settings.cloudtrail_enabled,
        "export_mode": settings.export_mode,
        "sync_user_triggered_only": True,
        "aws_clients_created": False,
        "report_formats": ["json"],
        "iam_write_capability": False,
    }


@router.post("/aws/sync/iam", response_model=SyncResult)
def sync_iam(body: AwsSyncRequest, settings: Local, database: Database, actor: Principal):
    if actor.tenant_id != settings.aws_tenant_id:
        raise AccessDenied()
    return AwsSyncService(
        AwsRepository(database, TenantScope(tenant_id=settings.aws_tenant_id), settings)
    ).sync("iam", body)


@router.post("/aws/sync/cloudtrail", response_model=SyncResult)
def sync_trail(body: CloudTrailRequest, settings: Local, database: Database, actor: Principal):
    if actor.tenant_id != settings.aws_tenant_id:
        raise AccessDenied()
    return AwsSyncService(
        AwsRepository(database, TenantScope(tenant_id=settings.aws_tenant_id), settings)
    ).sync("cloudtrail", body)


@router.get("/aws/imports/{run_id}", response_model=SyncResult)
def imported(run_id: GraphID, settings: Local, database: Database, actor: Principal):
    if actor.tenant_id != settings.aws_tenant_id:
        raise AccessDenied()
    repo = AwsRepository(database, TenantScope(tenant_id=settings.aws_tenant_id), settings)
    rows = repo.read(
        "MATCH (n:AwsSyncRun {tenant_id: $tenant_id,id: $id}) "
        "WHERE $account IS NULL OR n.account_id = $account RETURN n.response_json",
        {"id": run_id},
    )
    if not rows:
        raise GraphNotFound()
    return SyncResult.model_validate(json.loads(rows[0][0]))


@router.post("/aws/sync", response_model=SyncResult)
def sync(body: AwsSyncV1, settings: Local, database: Database, actor: Principal):
    if actor.tenant_id != settings.aws_tenant_id:
        raise AccessDenied()
    values = body.model_dump(exclude={"kind"})
    request = (
        AwsSyncRequest.model_validate(values)
        if body.kind == "iam"
        else CloudTrailRequest.model_validate(values)
    )
    return AwsSyncService(
        AwsRepository(database, TenantScope(tenant_id=actor.tenant_id), settings)
    ).sync(body.kind, request)


def reports(settings: Local, cases: Annotated[object, Depends(case_service)]):
    return ReportService(cases, settings)


Reports = Annotated[ReportService, Depends(reports)]


@router.post("/reports", response_model=ReportResult)
def export_report(body: ReportRequest, service: Reports):
    return service.export(body)


@router.get("/reports/{report_id}", response_model=ReportResult)
def report(report_id: GraphID, service: Reports):
    return service.saved(report_id)
