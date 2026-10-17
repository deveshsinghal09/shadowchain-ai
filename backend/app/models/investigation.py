"""SOC case lifecycle and traceable, non-executable investigation intelligence."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, field_validator

from app.models.graph import GraphModel, reject_secret
from app.models.memory import Key, MemoryRecord, ReadOnlyStep
from app.models.workflow import AgentRunRecord
from app.schemas.risk import GraphID

CASE_SOURCE = "shadowchain.investigations.v1"
CASE_VERSION = "investigations-2026.1"


class InvestigationStatus(StrEnum):
    NEW = "NEW"
    TRIAGING = "TRIAGING"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


TRANSITIONS = {
    InvestigationStatus.NEW: frozenset({InvestigationStatus.TRIAGING}),
    InvestigationStatus.TRIAGING: frozenset({InvestigationStatus.INVESTIGATING}),
    InvestigationStatus.INVESTIGATING: frozenset({InvestigationStatus.AWAITING_REVIEW}),
    InvestigationStatus.AWAITING_REVIEW: frozenset(
        {InvestigationStatus.INVESTIGATING, InvestigationStatus.RESOLVED}
    ),
    InvestigationStatus.RESOLVED: frozenset(
        {InvestigationStatus.INVESTIGATING, InvestigationStatus.CLOSED}
    ),
    InvestigationStatus.CLOSED: frozenset(),
}


class CaseCheck(StrEnum):
    RECONSTRUCT_HISTORY = "reconstruct_history"
    VERIFY_EVIDENCE = "verify_evidence_integrity"
    REVIEW_CONFLICTS = "review_memory_conflicts"
    DRAFT_RESPONSE = "draft_response"


CHECKS = tuple(CaseCheck)


class ReasonedRequest(GraphModel):
    request_id: Key
    expected_revision: int = Field(ge=0, strict=True)
    reason: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("reason")
    @classmethod
    def sanitized(cls, value: str) -> str:
        return reject_secret(value)


class TransitionCase(ReasonedRequest):
    status: InvestigationStatus
    resolution: Literal["accepted_risk", "false_positive"] | None = None


class ReviewCaseEvidence(ReasonedRequest):
    evidence_id: GraphID
    verification_status: Literal["verified", "disputed", "rejected"]
    polarity: Literal["supporting", "contradicting", "neutral"]


class ReviewHypothesis(ReasonedRequest):
    hypothesis_id: GraphID
    conclusion: Literal["supported", "rejected", "unresolved"]
    evidence_ids: list[GraphID] = Field(min_length=1, max_length=20)


class TransferCase(ReasonedRequest):
    recipient_agent_id: GraphID


class CaseTask(GraphModel):
    id: GraphID
    title: str
    task_type: str
    status: str
    agent_id: GraphID
    check_key: CaseCheck | None = None
    output: dict[str, Any] | None = None


class AuditEntry(GraphModel):
    id: GraphID
    action: str
    actor_agent_id: GraphID
    created_at: AwareDatetime
    details: dict[str, Any]


class TimelineEntry(GraphModel):
    id: str
    timestamp: AwareDatetime
    category: str
    title: str
    record_ids: list[GraphID]
    time_basis: Literal["source", "processing"] = "source"


class CaseSummary(GraphModel):
    investigation_id: GraphID
    severity: str
    status: InvestigationStatus
    revision: int
    owner_agent_id: GraphID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PriorIncident(GraphModel):
    investigation_id: GraphID
    status: str
    title: str
    shared_asset_ids: list[GraphID]
    similarity: float = Field(ge=0, le=1)
    basis: Literal["asset-set-jaccard"] = "asset-set-jaccard"


class HistoricalCheck(GraphModel):
    memory_id: GraphID
    steps: list[ReadOnlyStep]
    evidence_ids: list[GraphID]
    execution_allowed: Literal[False] = False


class TraceStatement(GraphModel):
    text: str
    record_ids: list[GraphID]


class Reconstruction(GraphModel):
    connected_asset_ids: list[GraphID]
    previous_incidents: list[PriorIncident]
    episodic_memory_ids: list[GraphID]
    procedural_memory_ids: list[GraphID]
    useful_historical_checks: list[HistoricalCheck]
    summary: list[TraceStatement]
    limitations: list[str]


class EvidenceView(GraphModel):
    id: GraphID
    provenance: dict[str, Any]
    content_sha256: str
    source_record: dict[str, Any] | None
    verification_status: Literal["unverified", "verified", "disputed", "rejected"]
    verification_scope: Literal["unreviewed", "source_integrity", "analyst_assessment"]
    polarity: Literal["supporting", "contradicting", "neutral"]
    review_decision_id: GraphID | None
    content_is_executable: Literal[False] = False


class HypothesisView(GraphModel):
    id: GraphID
    statement: str
    conclusion: Literal["unresolved", "supported", "rejected"]
    evidence_ids: list[GraphID]
    review_decision_id: GraphID | None
    classification: Literal["POTENTIAL ATTACK PATH"] = "POTENTIAL ATTACK PATH"


class CaseDetail(GraphModel):
    investigation_id: GraphID
    severity: str
    status: InvestigationStatus
    revision: int
    owner_agent_id: GraphID
    affected_identities: list[dict[str, Any]]
    affected_resources: list[dict[str, Any]]
    historical_change_timeline: list[TimelineEntry]
    contributing_security_events: list[dict[str, Any]]
    current_hypotheses: list[HypothesisView]
    verified_evidence: list[EvidenceView]
    contradictory_evidence: list[EvidenceView]
    unverified_evidence: list[EvidenceView]
    linked_previous_incidents: list[PriorIncident]
    assigned_agents: list[dict[str, Any]]
    completed_tasks: list[CaseTask]
    pending_tasks: list[CaseTask]
    memory_references: list[GraphID]
    recommended_response: list[dict[str, Any]]
    audit_history: list[AuditEntry]
    reconstruction: Reconstruction
    workflow_ids: list[GraphID]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    resolution: str | None
    limitations: list[str]


class CaseGraph(GraphModel):
    investigation_id: GraphID
    nodes: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    missing_record_ids: list[GraphID]
    layout_coordinates_included: Literal[False] = False


class CaseHandoff(GraphModel):
    handoff_id: GraphID
    investigation_id: GraphID
    sender_agent_id: GraphID
    recipient_agent_id: GraphID
    checkpoint: dict[str, Any]
    current_owner_agent_id: GraphID


class CaseMemories(GraphModel):
    investigation_id: GraphID
    memories: list[MemoryRecord]


class CaseList(GraphModel):
    schema_version: Literal["1"] = "1"
    investigations: list[CaseSummary]
    count: int
    snapshot_sequence: int = 0
    next_cursor: GraphID | None = None
    has_more: bool = False


class CaseTimeline(GraphModel):
    investigation_id: GraphID
    items: list[TimelineEntry]


class CaseActivity(GraphModel):
    investigation_id: GraphID
    agents: list[dict[str, Any]]
    completed_tasks: list[CaseTask]
    pending_tasks: list[CaseTask]
    runs: list[AgentRunRecord]
    audit: list[AuditEntry]
