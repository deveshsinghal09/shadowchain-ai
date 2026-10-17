from datetime import datetime

from pydantic import BaseModel, Field

from app.core.time import utc_now


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict[str, object]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str
    timestamp: datetime = Field(default_factory=utc_now)
