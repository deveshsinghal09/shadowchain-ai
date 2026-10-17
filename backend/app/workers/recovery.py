"""Bounded polling of persisted demo work; no process-local task queue is authoritative."""

import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from app.models.events import DEMO_TENANT
from app.models.graph import TenantScope
from app.repositories.workflow import WorkflowRepository
from app.services.workflow import InvestigationWorkflow

logger = logging.getLogger(__name__)


async def recover_workflows(database, settings, stop: asyncio.Event):
    workflow = InvestigationWorkflow(
        WorkflowRepository(database, TenantScope(tenant_id=DEMO_TENANT), settings)
    )
    while not stop.is_set():
        try:
            await run_in_threadpool(workflow.recover_pending)
        except Exception as exc:
            logger.warning(
                "workflow_recovery_unavailable", extra={"error_type": type(exc).__name__}
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.workflow_recovery_interval_seconds)
        except TimeoutError:
            pass
