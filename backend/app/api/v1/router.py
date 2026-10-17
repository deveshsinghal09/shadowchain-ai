from fastapi import APIRouter, Depends

from app.api.v1.agents import router as agents_router
from app.api.v1.aws import router as aws_router
from app.api.v1.contract import router as contract_router
from app.api.v1.demo import router as demo_router
from app.api.v1.health import router as health_router
from app.api.v1.investigations import router as investigations_router
from app.api.v1.live import router as live_router
from app.api.v1.memory import router as memory_router
from app.api.v1.remediation import router as remediation_router
from app.api.v1.risk import router as risk_router
from app.core.auth import principal
from app.schemas.errors import ErrorResponse

router = APIRouter(prefix="/api/v1")
router.include_router(health_router)
protected = APIRouter(
    dependencies=[Depends(principal)],
    responses={
        status: {"model": ErrorResponse} for status in [401, 403, 404, 409, 413, 422, 429, 500, 503]
    },
)
protected.include_router(contract_router)
protected.include_router(demo_router)
protected.include_router(risk_router)
protected.include_router(memory_router)
protected.include_router(agents_router)
protected.include_router(investigations_router)
protected.include_router(remediation_router)
protected.include_router(aws_router)
router.include_router(protected)
router.include_router(live_router)
