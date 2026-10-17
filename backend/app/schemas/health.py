from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.time import utc_now


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["shadowchain-ai"] = "shadowchain-ai"
    version: str
    demo_mode: bool
    timestamp: datetime = Field(default_factory=utc_now)


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    dependencies: dict[str, Literal["ok"]] = Field(default_factory=lambda: {"falkordb": "ok"})
    timestamp: datetime = Field(default_factory=utc_now)
