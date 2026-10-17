"""Version 1 committed change notifications; no untrusted content in stream payloads."""

from typing import Literal

from pydantic import AwareDatetime, Field, SecretStr

from app.models.graph import GraphModel
from app.schemas.errors import ErrorResponse
from app.schemas.risk import GraphID


class LiveEvent(GraphModel):
    schema_version: Literal["1"] = "1"
    event_id: GraphID
    sequence: int
    type: str
    occurred_at: AwareDatetime
    entity_ids: list[GraphID] = Field(max_length=64)
    references_truncated: bool = False


class LivePage(GraphModel):
    schema_version: Literal["1"] = "1"
    type: Literal["events"] = "events"
    events: list[LiveEvent] = Field(max_length=100)
    cursor: int
    high_watermark: int
    has_more: bool


class LiveAuthenticate(GraphModel):
    schema_version: Literal["1"] = "1"
    type: Literal["authenticate"]
    token: SecretStr
    after: int = Field(default=0, ge=0, le=9007199254740991, strict=True)
    limit: int = Field(default=50, ge=1, le=100, strict=True)


class LiveControl(GraphModel):
    schema_version: Literal["1"] = "1"
    type: Literal["hello", "heartbeat", "pong"]
    cursor: int
    high_watermark: int | None = None


class LivePing(GraphModel):
    type: Literal["ping"]


class LiveError(ErrorResponse):
    schema_version: Literal["1"] = "1"
    type: Literal["error"] = "error"
