"""Memory assertions are data. Review never turns source text into executable instructions."""

from datetime import UTC
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.models.graph import GraphModel, Provenance, Scalar, VerificationStatus, reject_secret
from app.schemas.risk import GraphID

MEMORY_VERSION = "memory-2026.1"
Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_./:-]+$")]


class MemoryType(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class MemoryRole(StrEnum):
    READER = "reader"
    WRITER = "writer"
    CURATOR = "curator"


class Audience(StrEnum):
    PRIVATE = "private"
    TEAM = "team"


class ReadOnlyStep(StrEnum):
    COLLECT_GRAPH_EVIDENCE = "collect_graph_evidence"
    QUERY_HISTORICAL_PATHS = "query_historical_paths"
    COMPARE_RISK_SNAPSHOTS = "compare_risk_snapshots"
    REVIEW_MEMORY_CONFLICTS = "review_memory_conflicts"
    DRAFT_REMEDIATION_PLAN = "draft_remediation_plan"


class MemoryClaim(GraphModel):
    subject_id: GraphID
    predicate: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    object_id: GraphID | None = None
    value: Scalar

    @field_validator("value")
    @classmethod
    def safe_scalar(cls, value: Scalar) -> Scalar:
        if isinstance(value, str):
            if len(value) > 1000:
                raise ValueError("Claim value exceeds size bound")
            reject_secret(value)
        if isinstance(value, float) and not float("-inf") < value < float("inf"):
            raise ValueError("Claim value must be finite")
        return value


class ProcedurePlan(GraphModel):
    steps: list[ReadOnlyStep] = Field(min_length=1, max_length=12)
    outcome: Literal["proposed", "successful", "failed", "lesson"]
    lesson: Annotated[str, Field(max_length=2000)] = ""

    @field_validator("lesson")
    @classmethod
    def no_credentials(cls, value: str) -> str:
        return reject_secret(value)


class MemoryWrite(GraphModel):
    request_id: Key
    memory_key: Key
    memory_type: MemoryType
    content: Annotated[str, Field(min_length=1, max_length=6000)]
    provenance: Provenance
    subject_ids: list[GraphID] = Field(default_factory=list, max_length=20)
    evidence_ids: list[GraphID] = Field(min_length=1, max_length=20)
    audience: Audience = Audience.PRIVATE
    confidence: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    claim: MemoryClaim | None = None
    procedure: ProcedurePlan | None = None

    @field_validator("content")
    @classmethod
    def no_credentials(cls, value: str) -> str:
        return reject_secret(value)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def utc_times(cls, value: AwareDatetime | None) -> AwareDatetime | None:
        return value.astimezone(UTC) if value else None

    @model_validator(mode="after")
    def consistent_payload(self) -> "MemoryWrite":
        if self.valid_to and self.valid_from and self.valid_to <= self.valid_from:
            raise ValueError("Memory interval must be nonempty")
        if (self.memory_type == MemoryType.PROCEDURAL) != (self.procedure is not None):
            raise ValueError("Procedural memory requires a structured read-only plan")
        for ids in (self.subject_ids, self.evidence_ids):
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate graph references")
        return self


class MemoryQuery(GraphModel):
    subject_ids: list[GraphID] = Field(default_factory=list, max_length=20)
    memory_ids: list[GraphID] = Field(default_factory=list, max_length=50)
    memory_type: MemoryType | None = None
    at: AwareDatetime | None = None
    include_superseded: bool = False
    max_hops: int = Field(default=0, ge=0, le=2, strict=True)
    limit: int = Field(default=50, ge=1, le=100, strict=True)


class ReviewMemory(GraphModel):
    request_id: Key
    memory_id: GraphID
    expected_version: int = Field(ge=1)
    verification_status: VerificationStatus
    reason: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("reason")
    @classmethod
    def no_credentials(cls, value: str) -> str:
        return reject_secret(value)


class SupersedeMemory(GraphModel):
    memory_id: GraphID
    expected_version: int = Field(ge=1)
    correction: MemoryWrite


class HandoffRequest(GraphModel):
    request_id: Key
    recipient_agent_id: GraphID
    subject_ids: list[GraphID] = Field(min_length=1, max_length=20)


class MemoryRecord(GraphModel):
    id: str
    memory_key: str
    memory_type: MemoryType
    version: int
    author_agent_id: str
    audience: Audience
    confidence: float
    verification_status: VerificationStatus
    state: Literal["current", "superseded"]
    content: str
    source_trust: Literal["untrusted_data"] = "untrusted_data"
    executable: Literal[False] = False
    created_at: AwareDatetime
    updated_at: AwareDatetime
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    provenance: Provenance
    claim: MemoryClaim | None
    procedure: ProcedurePlan | None
    subjects: list[dict]
    evidence: list[dict]
    conflicting_memory_ids: list[str]


class MemoryConflict(GraphModel):
    id: str
    memory_ids: list[str]
    claim_key: str
    current: bool


class ValidatedProcedure(GraphModel):
    memory_id: str
    steps: list[ReadOnlyStep]
    outcome: Literal["proposed", "successful", "failed", "lesson"]
    execution_allowed: Literal[False] = False


class AgentContext(GraphModel):
    agent_id: str
    space_id: str
    observations: list[MemoryRecord]
    established_facts: list[MemoryRecord]
    validated_procedures: list[ValidatedProcedure]
    read_only_capabilities: list[ReadOnlyStep]
    privileged_actions: tuple[()] = ()
    source_content_is_instructions: Literal[False] = False


class HandoffContext(GraphModel):
    handoff_id: str
    sender_agent_id: str
    recipient_agent_id: str
    memory_ids: list[str]
    context: AgentContext
